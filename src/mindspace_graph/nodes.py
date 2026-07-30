"""Small, testable LangGraph nodes for the Mindspace turn lifecycle."""

from __future__ import annotations

import time
from typing import Any

from langgraph.types import StreamWriter

from mindspace_graph.cancellation import GenerationCancelled
from mindspace_graph.capabilities import CapabilityPlan, enforce_capability_claims
from mindspace_graph.models import (
    ChatResponse,
    JsonUpdatePlan,
    JsonUpdateValidation,
    JsonWriteReceipt,
    ModelDiagnostics,
    ProtocolOutput,
    RoleValidation,
)
from mindspace_graph.policies import rank_with_temporal_decay
from mindspace_graph.ports import Dependencies
from mindspace_graph.profile_bootstrap import evaluate_profile_bootstrap
from mindspace_graph.prompting import build_prompt, resolve_initiative_request
from mindspace_graph.protocol import IncrementalResponseParser, ProtocolParser
from mindspace_graph.roleplay import allow_raw_chat_retrieval, normalize_voice_response
from mindspace_graph.state import TurnState
from mindspace_graph.voice_render import VoiceCueStream, extract_voice_cue, normalize_voice_cue


class NodeFactory:
    """LangGraph 节点实现。

    节点只返回状态增量；除 persist_turn 外不提交会话、档案或结构化记忆。
    StreamWriter 只负责发诊断/SSE 事件，不应被当成业务状态存储。
    """

    def __init__(self, dependencies: Dependencies):
        self.deps = dependencies
        self.parser = ProtocolParser()

    CALL_BUDGETS = {
        "planner": 1,
        "research_review": 1,
        "generation": 1,
    }
    TOTAL_CALL_BUDGET = 3

    @classmethod
    def _call_allowed(cls, state: TurnState, kind: str) -> bool:
        """Apply per-purpose and total limits without coupling unrelated model calls."""

        counts = state.get("llm_call_counts", {})
        total = sum(int(value) for value in counts.values())
        return total < cls.TOTAL_CALL_BUDGET and int(counts.get(kind, 0)) < int(
            cls.CALL_BUDGETS[kind]
        )

    @classmethod
    def _record_call(
        cls,
        state: TurnState,
        kind: str,
        started: float,
        *,
        status: str = "success",
        error: str = "",
    ) -> dict[str, Any]:
        counts = dict(state.get("llm_call_counts", {}))
        counts[kind] = int(counts.get(kind, 0)) + 1
        summary = [
            *state.get("model_call_summary", []),
            {
                "kind": kind,
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": error[:500],
            },
        ]
        return {
            "llm_call_counts": counts,
            "llm_call_count": sum(counts.values()),
            "model_call_summary": summary,
        }

    @staticmethod
    def _model_diagnostics(state: TurnState) -> ModelDiagnostics:
        return ModelDiagnostics(
            call_summary=state.get("model_call_summary", []),
            total_calls=state.get("llm_call_count", 0),
        )

    def _check_cancelled(self, state: TurnState) -> None:
        request_id = state.get("request_id", "")
        if self.deps.cancellation and self.deps.cancellation.is_cancelled(request_id):
            raise GenerationCancelled(f"generation cancelled: {request_id}")

    def validate_request(self, state: TurnState) -> dict[str, Any]:
        self._check_cancelled(state)
        request = state["request"]
        self.deps.audit.record(
            "turn_started",
            {"session_id": request.session_id, "round": request.round, "mode": request.mode},
        )
        return {
            "llm_call_count": 0,
            "llm_call_counts": {},
            "model_call_summary": [],
            "trace": ["validate_request"],
        }

    def load_context(self, state: TurnState) -> dict[str, Any]:
        """读取本轮一致性快照，不调用模型，也不无条件采集本机状态。"""

        self._check_cancelled(state)
        request = state["request"]
        profiles = self.deps.profiles.load_bundle(request.character_id)
        request = resolve_initiative_request(request, profiles)
        deletion_events = self.deps.sessions.load_pending_deletions(request.session_id)
        recent_history = self.deps.sessions.load_all(request.session_id)
        if request.mode == "regenerate":
            recent_history = [
                item for item in recent_history if int(item.get("round", 0)) != request.round
            ]
            if self.deps.context is not None:
                self.deps.context.invalidate(
                    request.session_id,
                    reason="round_regenerated",
                    details={"round": request.round},
                )
        previous_emotion_state = None
        if (
            request.interaction_mode == "voice"
            and self.deps.emotion is not None
            and self.deps.emotion.enabled()
        ):
            previous = getattr(self.deps.emotion, "previous_for_round", None)
            if callable(previous):
                previous_emotion_state = previous(request.session_id, request.round)
        result = {
            "request": request,
            "profiles": profiles,
            "deletion_events": deletion_events,
            "recent_history": recent_history,
            "profile_bootstrap": evaluate_profile_bootstrap(
                request,
                profiles,
                recent_history,
                has_pending_deletions=bool(deletion_events),
            ),
            "trace": ["load_context"],
        }
        if previous_emotion_state is not None:
            result["emotion_state"] = previous_emotion_state
        return result

    def retrieve_knowledge(self, state: TurnState) -> dict[str, Any]:
        """知识库召回分支；与 retrieve_chat 并行，分支内保持只读。"""

        self._check_cancelled(state)
        request = state["request"]
        settings = request.retrieval
        chunks = []
        if not settings.ready:
            return {
                "knowledge_chunks": [],
                "trace": ["retrieve_knowledge_deferred"],
            }
        capability_allowed = (
            self.deps.capabilities is None
            or self.deps.capabilities.enabled("local_knowledge_enabled")
        )
        if capability_allowed and settings.rag_enabled and settings.knowledge_enabled:
            query = request.message
            chunks = self.deps.retriever.search_knowledge(
                query,
                settings.knowledge_k * settings.candidate_multiplier,
                settings=settings,
                user_name=request.user_name,
                character_name=request.character_name,
            )
            chunks = [item for item in chunks if item.score >= settings.similarity_threshold]
        return {"knowledge_chunks": chunks, "trace": ["retrieve_knowledge"]}

    def retrieve_chat(self, state: TurnState) -> dict[str, Any]:
        """当前会话与结构化记忆召回分支；不重复执行知识库召回。"""

        self._check_cancelled(state)
        request = state["request"]
        settings = request.retrieval
        chunks = []
        if not settings.ready:
            return {
                "chat_chunks": [],
                "trace": ["retrieve_chat_deferred"],
            }
        capability_allowed = (
            self.deps.capabilities is None
            or self.deps.capabilities.enabled("local_knowledge_enabled")
        )
        if capability_allowed and settings.rag_enabled and settings.chat_enabled:
            query = request.message
            chunks = self.deps.retriever.search_chat(
                query,
                request.session_id,
                settings.chat_k * settings.candidate_multiplier,
                character_id=request.character_id,
                settings=settings,
                user_name=request.user_name,
                character_name=request.character_name,
                messages=state.get("recent_history", []),
                include_raw_chat=allow_raw_chat_retrieval(request),
            )
            chunks = [item for item in chunks if item.score >= settings.similarity_threshold]
            if self.deps.activities is not None:
                narratives = self.deps.activities.search_narratives(
                    request.character_id,
                    query,
                    limit=min(3, settings.memory_family_limit),
                )
                chunks.extend(
                    item
                    for item in narratives
                    if item.score >= settings.similarity_threshold
                )
        if not settings.structured_memory_enabled:
            chunks = [item for item in chunks if item.source != "memory"]
        return {"chat_chunks": chunks, "trace": ["retrieve_chat"]}

    def rank_context(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        combined = state.get("knowledge_chunks", []) + state.get("chat_chunks", [])
        request = state["request"]
        limit = request.retrieval.knowledge_k + request.retrieval.chat_k
        ranked = rank_with_temporal_decay(combined, request, limit=limit)
        self.deps.retriever.record_retrieval(combined, ranked, request.round)
        writer(
            {
                "event": "retrieval.completed",
                "data": {
                    "knowledge": len(state.get("knowledge_chunks", [])),
                    "chat": len(state.get("chat_chunks", [])),
                    "ranked": [item.model_dump(mode="json") for item in ranked],
                    "ready": request.retrieval.ready,
                    "deferred_reason": request.retrieval.deferred_reason,
                },
            }
        )
        trace = "rank_context" if request.retrieval.ready else "rank_context_deferred"
        return {"ranked_context": ranked, "trace": [trace]}

    def capability_route(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """先用确定性规则路由能力，再判断是否值得支付一次私有规划调用。

        任何 web.* 计划都会进入 preflight，用于消解口语、省略指代和搜索词；
        明确的网页或知识请求通常可直接进入执行器。
        """

        self._check_cancelled(state)
        service = self.deps.capabilities
        if service is None:
            plan = CapabilityPlan()
            available: list[dict[str, Any]] = []
            policy: dict[str, Any] = {}
        else:
            plan = service.route(
                state["request"],
                history=state.get("recent_history", []),
            )
            available = service.definitions()
            policy = service.prompt_policy()
        preflight_required = plan.decision == "needs_planner" or any(
            call.capability.startswith("web.") for call in plan.calls
        )
        writer(
            {
                "event": "capability.routing",
                "data": {
                    "decision": plan.decision,
                    "reason": plan.reason,
                    "call_count": len(plan.calls),
                    "emotion_deferred": bool(
                        state["request"].interaction_mode == "voice"
                        and self.deps.emotion is not None
                        and self.deps.emotion.enabled()
                    ),
                },
            }
        )
        return {
            "available_capabilities": available,
            "capability_policy": policy,
            "capability_plan": plan,
            # Keep the state shape stable even when ordinary chat bypasses the
            # two empty execution/review nodes.
            "capability_results": [],
            "capability_notice": "",
            "preflight_required": preflight_required,
            "trace": ["capability_route"],
        }

    @staticmethod
    def route_capability_plan(state: TurnState) -> str:
        if state.get("preflight_required", False):
            return "planner"
        plan = state.get("capability_plan") or CapabilityPlan()
        if plan.decision == "use_capabilities" and plan.calls:
            return "execute"
        # Ordinary companion chat should not traverse two empty tool nodes.
        return "compose"

    def plan_capabilities(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """执行一次非流式私有规划；失败时只保留服务端能够确定授权的调用。"""

        self._check_cancelled(state)
        service = self.deps.capabilities
        if service is None:
            return {"capability_plan": CapabilityPlan(), "trace": ["plan_capabilities"]}
        request = state["request"]
        base_plan = state.get("capability_plan") or CapabilityPlan()
        # 能力规划使用独立超时，不能继承情绪支链的短 deadline；否则天气等查询会在
        # 500 ms 左右错误降级，并把未消解的口语原文直接当作搜索词。
        deadline_seconds = 12.0
        planner = getattr(self.deps.llm, "preflight", None)
        legacy_planner = getattr(self.deps.llm, "plan_capabilities", None)
        planner = planner if callable(planner) else legacy_planner
        if not callable(planner) or not self._call_allowed(state, "planner"):
            return {
                "capability_plan": service.authorize(base_plan),
                "trace": ["plan_capabilities"],
            }

        started = time.perf_counter()
        plan = service.authorize(base_plan)
        usages = list(state.get("model_usage", []))
        planner_error = ""
        try:
            messages = service.planner_messages(
                request,
                base_plan=base_plan,
                history=state.get("recent_history", []),
            )
            if callable(getattr(self.deps.llm, "preflight", None)):
                raw = self.deps.llm.preflight(
                    messages,
                    request.api,
                    timeout_seconds=deadline_seconds,
                )
            else:
                raw = legacy_planner(messages, request.api)
            plan = service.parse_preflight_output(raw, base_plan=base_plan)
            take_usage = getattr(self.deps.llm, "take_usage", None)
            usage = take_usage() if callable(take_usage) else None
            if usage is not None:
                usages.append(usage)
                writer({"event": "model.usage", "data": usage.model_dump(mode="json")})
        except Exception as exc:  # noqa: BLE001 - planning failure uses safe deterministic fallback
            planner_error = str(exc)
            # An explicit deterministic request is already resolved and safe to
            # execute. Only ambiguous planner-dependent web calls are discarded.
            retained = (
                list(base_plan.calls)
                if base_plan.reason == "deterministic_route"
                else [
                    call
                    for call in base_plan.calls
                    if not call.capability.startswith("web.") or call.capability == "web.open"
                ]
            )
            plan = service.authorize(
                CapabilityPlan(
                    decision="use_capabilities" if retained else "direct_answer",
                    reason="planner_unavailable",
                    calls=retained,
                    objective="可靠解析用户要查询的具体主题",
                    requires_clarification=not retained,
                    clarification_question=(
                        "我还没可靠确定你要查的具体内容；请再说一次主题，"
                        "如果是天气，请同时告诉我城市。"
                        if not retained
                        else ""
                    ),
                    follow_up_allowed=False,
                )
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        if planner_error:
            self.deps.audit.record(
                "preflight_degraded",
                {"request_id": state.get("request_id", ""), "error": planner_error},
            )
        writer(
            {
                "event": "capability.planned",
                "data": {
                    "decision": plan.decision,
                    "reason": plan.reason,
                    "call_count": len(plan.calls),
                    "resolved_query": plan.resolved_query,
                    "requires_clarification": plan.requires_clarification,
                    "calls": [call.model_dump(mode="json") for call in plan.calls],
                    "elapsed_ms": elapsed_ms,
                },
            }
        )
        update: dict[str, Any] = {
            "capability_plan": plan,
            "model_usage": usages,
            "trace": ["plan_capabilities"],
        }
        update.update(
            self._record_call(
                state,
                "planner",
                started,
                status="degraded" if planner_error else "success",
                error=planner_error,
            )
        )
        return update

    def execute_capabilities(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """按授权计划顺序串行执行只读调用。"""

        self._check_cancelled(state)
        service = self.deps.capabilities
        plan = state.get("capability_plan") or CapabilityPlan()
        if service is None or plan.decision != "use_capabilities" or not plan.calls:
            return {
                "capability_results": [],
                "capability_notice": "",
                "trace": ["execute_capabilities"],
            }
        plan = service.authorize(plan)
        notice = service.notice(plan)
        if notice:
            writer(
                {
                    "event": "capability.notice",
                    "data": {"label": notice, "single_turn": True, "transient": True},
                }
            )
        for call in plan.calls:
            writer(
                {
                    "event": "capability.started",
                    "data": {
                        "call_id": call.call_id,
                        "capability": call.capability,
                        "arguments": call.arguments,
                        "single_turn": True,
                    },
                }
            )
        results = service.execute(
            plan,
            ranked_context=state.get("ranked_context", []),
        )
        for result in results:
            writer(
                {
                    "event": (
                        "capability.completed"
                        if result.status == "success"
                        else "capability.failed"
                    ),
                    "data": {
                        "call_id": result.call_id,
                        "capability": result.capability,
                        "status": result.status,
                        "error": result.error,
                        "observed_at": result.observed_at,
                        "output": result.data,
                        "trust": result.trust,
                        "included_in_main_prompt": True,
                        "single_turn": True,
                    },
                }
            )
        return {
            "capability_plan": plan,
            "capability_results": results,
            "capability_notice": notice,
            "trace": ["execute_capabilities"],
        }

    def review_capabilities(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """仅在第一波网页证据覆盖不足时，允许一次有上限的补查规划。

        补查仍发生在唯一一次用户可见回答之前，不形成无界研究循环。
        """

        self._check_cancelled(state)
        service = self.deps.capabilities
        plan = state.get("capability_plan") or CapabilityPlan()
        results = list(state.get("capability_results", []))
        has_web = any(result.capability.startswith("web.") for result in results)
        reviewer = getattr(self.deps.llm, "review_research", None)
        review_required = bool(
            service is not None
            and service.research_review_required(plan, results)
        )
        if (
            service is None
            or not has_web
            or not review_required
            or not callable(reviewer)
            or not self._call_allowed(state, "research_review")
        ):
            return {"trace": ["review_capabilities"]}
        started = time.perf_counter()
        try:
            messages = service.research_review_messages(
                state["request"],
                history=state.get("recent_history", []),
                plan=plan,
                results=results,
            )
            raw = reviewer(messages, state["request"].api, timeout_seconds=10.0)
            follow_up = service.parse_research_review(raw, completed_plan=plan)
            usage = self._take_model_usage(writer)
            usages = [*state.get("model_usage", []), *([usage] if usage else [])]
            if follow_up.decision == "use_capabilities" and follow_up.calls:
                for call in follow_up.calls:
                    writer(
                        {
                            "event": "capability.started",
                            "data": {
                                "call_id": call.call_id,
                                "capability": call.capability,
                                "arguments": call.arguments,
                                "phase": "follow_up",
                                "single_turn": True,
                            },
                        }
                    )
                extra = service.execute(
                    follow_up,
                    ranked_context=state.get("ranked_context", []),
                )
                results.extend(extra)
                for result in extra:
                    writer(
                        {
                            "event": (
                                "capability.completed"
                                if result.status == "success"
                                else "capability.failed"
                            ),
                            "data": {
                                "call_id": result.call_id,
                                "capability": result.capability,
                                "status": result.status,
                                "error": result.error,
                                "observed_at": result.observed_at,
                                "output": result.data,
                                "trust": result.trust,
                                "included_in_main_prompt": True,
                                "phase": "follow_up",
                                "single_turn": True,
                            },
                        }
                    )
            writer(
                {
                    "event": "capability.reviewed",
                    "data": {
                        "follow_up_count": len(follow_up.calls),
                        "reason": follow_up.reason,
                        "calls": [call.model_dump(mode="json") for call in follow_up.calls],
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                }
            )
            update = {
                "capability_results": results,
                "model_usage": usages,
                "trace": ["review_capabilities"],
            }
            update.update(self._record_call(state, "research_review", started))
            return update
        except Exception as exc:  # noqa: BLE001 - first-pass evidence remains usable
            self.deps.audit.record(
                "research_review_degraded",
                {"request_id": state.get("request_id", ""), "error": str(exc)},
            )
            writer(
                {
                    "event": "capability.reviewed",
                    "data": {"follow_up_count": 0, "degraded": True, "error": str(exc)[:300]},
                }
            )
            return {
                **self._record_call(
                    state,
                    "research_review",
                    started,
                    status="degraded",
                    error=str(exc),
                ),
                "trace": ["review_capabilities"],
            }

    def compose_prompt(self, state: TurnState) -> dict[str, Any]:
        """把权威数据、账本历史和本轮临时上下文组装成主模型 messages。"""

        self._check_cancelled(state)
        built = build_prompt(
            state["request"],
            state["profiles"],
            state.get("recent_history", []),
            state.get("ranked_context", []),
            state.get("deletion_events", []),
            state.get("profile_bootstrap"),
            state.get("available_capabilities", []),
            state.get("capability_results", []),
            state.get("capability_policy", {}),
            state.get("capability_plan"),
            state.get("emotion_state"),
            context_ledger=self.deps.context,
        )
        snapshot = built.context_snapshot
        if self.deps.prompt_inspector is not None:
            # Inspection observes the already-built message list and cannot
            # modify ordering, cache layout, or the provider request.
            self.deps.prompt_inspector.record(
                run_id=state.get("request_id", ""),
                session_id=state["request"].session_id,
                character_id=state["request"].character_id,
                messages=built.messages,
                pending_events=built.pending_events,
            )
        if snapshot and snapshot.emergency_truncated:
            self.deps.audit.record(
                "context_emergency_truncated",
                {
                    "session_id": state["request"].session_id,
                    "epoch_id": snapshot.epoch_id,
                    "estimated_tokens": snapshot.estimated_tokens,
                },
            )
        return {
            "prompt_messages": built.messages,
            "prompt_pending_events": built.pending_events,
            "context_epoch_id": snapshot.epoch_id if snapshot else 0,
            "context_estimated_tokens": snapshot.estimated_tokens if snapshot else 0,
            "context_emergency_truncated": (snapshot.emergency_truncated if snapshot else False),
            "trace": ["compose_prompt"],
        }

    def generate_candidate(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """调用主模型并只把 <response> 内正文增量暴露给前端。"""

        self._check_cancelled(state)
        if not self._call_allowed(state, "generation"):
            raise RuntimeError("generation model call budget exhausted")
        request = state["request"]
        # The primary generation remains the only foreground content call.
        # R18 intensity and stage advancement are decided in the final prompt,
        # never by a second rewrite request.
        defer_visible_response = False
        started = time.perf_counter()
        extractor = IncrementalResponseParser()
        # Retain backward-compatible cue parsing for older model templates.
        # The Base character voice no longer requires a leading cue.
        voice_cue_stream = VoiceCueStream(allow_adult=request.adult_mode)
        active_tts_provider = (
            self.deps.tts_provider()
            if callable(self.deps.tts_provider)
            else self.deps.tts_provider
        )
        emit_voice_cue = (
            request.interaction_mode == "voice"
            and active_tts_provider == "qwen3-vllm"
        )
        voice_cue_sent = False
        chunks: list[str] = []
        for token in self.deps.llm.stream(state["prompt_messages"], request.api):
            self._check_cancelled(state)
            chunks.append(token)
            if not defer_visible_response:
                for delta in extractor.feed(token):
                    deltas = voice_cue_stream.feed(delta)
                    if emit_voice_cue and voice_cue_stream.resolved and not voice_cue_sent:
                        writer(
                            {
                                "event": "response.voice_cue",
                                "data": {"cue": voice_cue_stream.cue},
                            }
                        )
                        voice_cue_sent = True
                    for spoken_delta in deltas:
                        normalized = normalize_voice_response(spoken_delta, request)
                        if normalized:
                            writer({"event": "response.delta", "data": {"delta": normalized}})
        if not defer_visible_response:
            for delta in voice_cue_stream.flush():
                if emit_voice_cue and not voice_cue_sent:
                    writer({"event": "response.voice_cue", "data": {"cue": voice_cue_stream.cue}})
                    voice_cue_sent = True
                normalized = normalize_voice_response(delta, request)
                if normalized:
                    writer({"event": "response.delta", "data": {"delta": normalized}})
        raw = "".join(chunks)
        usage = self._take_model_usage(writer)
        self._check_cancelled(state)
        return {
            "raw_candidate": raw,
            "voice_cue": voice_cue_stream.cue,
            "voice_cue_event_sent": voice_cue_sent,
            "model_usage": [*state.get("model_usage", []), *([usage] if usage else [])],
            **self._record_call(state, "generation", started),
            "trace": ["generate_candidate"],
        }

    def _take_model_usage(self, writer: StreamWriter):
        take_usage = getattr(self.deps.llm, "take_usage", None)
        usage = take_usage() if callable(take_usage) else None
        if usage is not None:
            writer({"event": "model.usage", "data": usage.model_dump(mode="json")})
        return usage

    def parse_protocol(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """在完整输出结束后解析协议，并对虚构的能力执行声明做确定性兜底。"""

        self._check_cancelled(state)
        raw = state.get("raw_candidate", "")
        request = state["request"]
        parsed_protocol, errors = self.parser.parse(raw)
        previous_visible = state.get("fallback_response")
        parsed_visible = self.parser.response_text(raw)
        parsed_voice_cue, _spoken_response, explicit_cue = extract_voice_cue(
            parsed_visible or "", allow_adult=request.adult_mode
        )
        # Adult/director runs intentionally defer visible deltas, so their cue
        # must be recovered from the completed response instead of inheriting
        # the stream parser's neutral default.
        voice_cue = parsed_voice_cue if explicit_cue else normalize_voice_cue(
            state.get("voice_cue"), allow_adult=request.adult_mode
        )
        active_tts_provider = (
            self.deps.tts_provider()
            if callable(self.deps.tts_provider)
            else self.deps.tts_provider
        )
        emit_voice_cue = (
            request.interaction_mode == "voice"
            and active_tts_provider == "qwen3-vllm"
        )
        if emit_voice_cue and not state.get("voice_cue_event_sent"):
            writer({"event": "response.voice_cue", "data": {"cue": voice_cue}})
        visible_response = parsed_visible or previous_visible
        if visible_response:
            visible_response = normalize_voice_response(visible_response, request)
        # Model-authored profile patches are disabled.  The model may still
        # emit a legacy <json_update> block, but the server ignores it and
        # always owns an empty plan.  This also makes protocol formatting
        # incapable of triggering a second foreground model call.
        protocol = self._safe_protocol(visible_response, state) if visible_response else None
        if protocol is not None:
            self.deps.audit.record(
                "protocol_fallback",
                {
                    "request_id": state.get("request_id", ""),
                    "errors": errors,
                    "reason": (
                        "model_json_update_ignored"
                        if parsed_protocol is not None
                        else "visible_response_recovered"
                    ),
                },
            )
            errors = []
        update: dict[str, Any] = {
            "protocol_errors": errors,
            "voice_cue": voice_cue,
            "voice_cue_event_sent": bool(
                emit_voice_cue or state.get("voice_cue_event_sent")
            ),
            "trace": ["parse_protocol"],
        }
        if visible_response:
            update["fallback_response"] = visible_response
        if protocol is not None:
            normalized_response = normalize_voice_response(protocol.response, request)
            if normalized_response != protocol.response:
                protocol = protocol.model_copy(update={"response": normalized_response})
                update["fallback_response"] = normalized_response
            guarded_response, capability_violations = enforce_capability_claims(
                protocol.response,
                plan=state.get("capability_plan"),
                results=state.get("capability_results", []),
            )
            if capability_violations and guarded_response != protocol.response:
                protocol = protocol.model_copy(update={"response": guarded_response})
                update["fallback_response"] = guarded_response
                self.deps.audit.record(
                    "unverified_capability_claim_blocked",
                    {
                        "request_id": state.get("request_id", ""),
                        "claims": capability_violations[:4],
                    },
                )
                writer(
                    {
                        "event": "response.replace",
                        "data": {
                            "content": guarded_response,
                            "reason": "unverified_capability_claim_blocked",
                        },
                    }
                )
            update["protocol"] = protocol
            # The visible reply is now complete, bracket-cleaned and guarded.
            # Let full-context TTS start here instead of waiting for role
            # diagnostics, persistence and the terminal run.completed event.
            writer(
                {
                    "event": "response.ready",
                    "data": {
                        "content": protocol.response,
                        "voice_cue": voice_cue,
                    },
                }
            )
        return update

    @staticmethod
    def _safe_protocol(response: str, state: TurnState) -> ProtocolOutput:
        request = state["request"]
        return ProtocolOutput(
            response=response,
            json_update=JsonUpdatePlan(
                turn_id=f"round_{request.round}",
                base_revisions=state["profiles"].revisions,
                trigger="none",
                patches=[],
            ),
        )

    def validate_role(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        request = state["request"]
        result = self.deps.role_policy.validate(
            state["protocol"].response,
            request=request,
            history=state.get("recent_history", []),
        )
        writer(
            {
                "event": "validation.completed",
                "data": {"kind": "role", **result.model_dump(mode="json")},
            }
        )
        if not result.is_valid:
            self.deps.audit.record(
                "foreground_role_diagnostic",
                {
                    "request_id": state.get("request_id", ""),
                    "message": result.message,
                    "suggestion": result.suggestion,
                },
            )
        return {"role_validation": result, "trace": ["validate_role"]}

    def validate_json_update(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """Install the server-owned no-profile-write plan.

        Direct profile APIs remain user-controlled and revisioned.  Foreground
        chat no longer asks the model to infer or patch user/AI/runtime JSON;
        recent continuity is produced by the non-blocking post-turn worker.
        """

        self._check_cancelled(state)
        request = state["request"]
        plan = JsonUpdatePlan(
            turn_id=f"round_{request.round}",
            base_revisions=state["profiles"].revisions,
            trigger="none",
            patches=[],
        )
        result = JsonUpdateValidation(is_valid=True, normalized_plan=plan)
        self.deps.audit.record(
            "json_update_validated",
            {
                "valid": True,
                "errors": [],
                "trigger": "none",
                "extraction_attempted": False,
                "model_profile_writes_enabled": False,
            },
        )
        writer(
            {
                "event": "validation.completed",
                "data": {
                    "kind": "json_update",
                    "trigger": "none",
                    "patch_count": 0,
                    "model_profile_writes_enabled": False,
                    **result.model_dump(mode="json", exclude={"normalized_plan"}),
                },
            }
        )
        return {
            "json_update_plan": plan,
            "json_update_validation": result,
            "trace": ["validate_json_update"],
        }

    def persist_turn(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """本轮唯一提交入口；有数据库时把所有写入放进同一事务。"""

        if self.deps.database is not None:
            request = state["request"]
            with self.deps.database.transaction(
                operation="persist_turn",
                details={
                    "session_id": request.session_id,
                    "round": request.round,
                    "request_id": state.get("request_id", ""),
                },
            ):
                return self._persist_turn(state, writer)
        return self._persist_turn(state, writer)

    def _persist_turn(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        request = state["request"]
        protocol = state["protocol"]
        existing_commit = (
            self.deps.context.find_turn_commit(state.get("request_id", ""))
            if self.deps.context is not None
            else None
        )
        if existing_commit is not None:
            if (
                str(existing_commit["session_id"]) != request.session_id
                or int(existing_commit["round_num"]) != request.round
            ):
                raise ValueError("request_id already belongs to another turn")
            assistant_id = str(existing_commit["assistant_message_id"])
            existing_message = next(
                (
                    item
                    for item in state.get("recent_history", [])
                    if item.get("message_id") == assistant_id
                ),
                None,
            )
            reply = (
                str(existing_message.get("content") or protocol.response)
                if existing_message
                else protocol.response
            )
            response = ChatResponse(
                session_id=request.session_id,
                round=request.round,
                status="success",
                reply=reply,
                assistant_message_id=assistant_id,
                trace=[*state.get("trace", []), "persist_turn_idempotent"],
                llm_call_count=state.get("llm_call_count", 0),
                model_usage=state.get("model_usage", []),
                model=self._model_diagnostics(state),
            )
            writer(
                {
                    "event": "json_update.committed",
                    "data": {"persisted": True, "idempotent_replay": True},
                }
            )
            return {
                "writeback_applied": False,
                "response": response,
                "trace": ["persist_turn_idempotent"],
            }
        primary_commit_allowed = request.mode == "primary" and not request.initiative
        validation = state.get("json_update_validation") or JsonUpdateValidation(is_valid=False)
        receipt = JsonWriteReceipt(turn_id=f"round_{request.round}")
        if (
            primary_commit_allowed
            and validation.is_valid
            and validation.normalized_plan
            and validation.normalized_plan.patches
        ):
            receipt = self.deps.profiles.apply_json_update(
                validation.normalized_plan,
                request=request,
            )
        persisted = self.deps.sessions.persist_turn(
            request,
            protocol.response,
            replace_round=request.mode == "regenerate",
            write_receipt=receipt,
        )
        memory_stats: dict[str, int] = {}
        if self.deps.memory is not None and request.mode == "primary" and not request.initiative:
            try:
                memory_stats = self.deps.memory.record_turn(
                    request,
                    protocol.response,
                    persisted=persisted,
                    write_receipt=receipt,
                )
            except Exception as exc:  # noqa: BLE001 - memory indexing must not lose the turn
                self.deps.audit.record(
                    "structured_memory_failed",
                    {"session_id": request.session_id, "round": request.round, "error": str(exc)},
                )
                if self.deps.database is not None:
                    raise
        context_commit: dict[str, int] = {}
        if self.deps.context is not None and state.get("context_epoch_id"):
            try:
                pending_events = [
                    dict(item)
                    for item in state.get("prompt_pending_events", [])
                    if not item.get("ephemeral")
                ]
                for item in pending_events:
                    if item.get("kind") == "current_user":
                        metadata = dict(item.get("metadata") or {})
                        metadata["message_id"] = persisted["user_message_id"]
                        item["metadata"] = metadata
                current_profiles = self.deps.profiles.load_bundle(request.character_id)
                context_commit = self.deps.context.append_turn(
                    request_id=state.get("request_id")
                    or f"{request.session_id}:{request.round}:{persisted['assistant_message_id']}",
                    session_id=request.session_id,
                    round_num=request.round,
                    epoch_id=state["context_epoch_id"],
                    pending_events=pending_events,
                    response=protocol.response,
                    user_message_id=persisted["user_message_id"],
                    assistant_message_id=persisted["assistant_message_id"],
                    receipt=receipt,
                    profiles=current_profiles,
                )
                self.deps.context.record_model_usage(
                    request_id=state.get("request_id")
                    or f"{request.session_id}:{request.round}:{persisted['assistant_message_id']}",
                    session_id=request.session_id,
                    round_num=request.round,
                    usages=state.get("model_usage", []),
                )
                if self.deps.role_audit_enabled:
                    self.deps.context.enqueue_role_audit(
                        session_id=request.session_id,
                        round_num=request.round,
                        payload={
                            "character_name": request.character_name,
                            "configured_system_prompt": request.system_prompt,
                            "authoritative_ai_profile": current_profiles.ai_profile,
                            "interaction_mode": request.interaction_mode,
                            "user_message": request.message,
                            "assistant_response": protocol.response,
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - next turn rebuilds from raw session data
                self.deps.audit.record(
                    "context_ledger_rebuild_required",
                    {
                        "session_id": request.session_id,
                        "round": request.round,
                        "error": str(exc),
                    },
                )
                if self.deps.database is not None:
                    raise
        deletion_events = state.get("deletion_events", [])
        resolved_ids: list[str] = []
        deletion_decision_complete = protocol.json_update.trigger in {
            "none",
            "deletion_reconciliation",
        }
        if (
            primary_commit_allowed
            and validation.is_valid
            and deletion_events
            and deletion_decision_complete
        ):
            resolved_ids = [event.event_id for event in deletion_events]
            self.deps.sessions.resolve_deletions(resolved_ids)

        response = ChatResponse(
            session_id=request.session_id,
            round=request.round,
            status="success",
            reply=protocol.response,
            assistant_message_id=persisted["assistant_message_id"],
            writeback_applied=receipt.applied,
            retrieval_counts={
                "knowledge": sum(
                    item.source == "knowledge" for item in state.get("ranked_context", [])
                ),
                "chat": sum(
                    item.source in {"chat", "memory"} for item in state.get("ranked_context", [])
                ),
            },
            errors=validation.errors,
            trace=[*state.get("trace", []), "persist_turn"],
            llm_call_count=state.get("llm_call_count", 0),
            model_usage=state.get("model_usage", []),
            model=self._model_diagnostics(state),
        )
        self.deps.audit.record("turn_completed", response.model_dump(mode="json"))
        writer(
            {
                "event": "json_update.committed",
                "data": {
                    "persisted": True,
                    "writeback_applied": receipt.applied,
                    "patch_count": len(receipt.patches),
                    "resolved_deletion_event_ids": resolved_ids,
                    "structured_memory": memory_stats,
                    "context_commit": context_commit,
                },
            }
        )
        return {
            "writeback_applied": receipt.applied,
            "response": response,
            "trace": ["persist_turn"],
        }

    def finalize_error(self, state: TurnState) -> dict[str, Any]:
        request = state["request"]
        role = state.get("role_validation") or RoleValidation(
            is_valid=False, message="role validation not reached"
        )
        errors = [*state.get("protocol_errors", [])]
        if not role.is_valid and role.message:
            errors.append(role.message)
        response = ChatResponse(
            session_id=request.session_id,
            round=request.round,
            status="error",
            errors=errors or ["generation failed validation"],
            trace=[*state.get("trace", []), "finalize_error"],
            llm_call_count=state.get("llm_call_count", 0),
            model=self._model_diagnostics(state),
        )
        self.deps.audit.record("turn_failed", response.model_dump(mode="json"))
        return {"response": response, "errors": errors, "trace": ["finalize_error"]}

    def route_protocol(self, state: TurnState) -> str:
        """Accept visible text once; malformed model-only output fails once."""

        if state.get("protocol") is not None and not state.get("protocol_errors"):
            return "valid"
        return "fail"
