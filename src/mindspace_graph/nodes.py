"""Small, testable LangGraph nodes for the Mindspace turn lifecycle."""

from __future__ import annotations

import re
import time
from typing import Any

from langgraph.types import StreamWriter

from mindspace_graph.cancellation import GenerationCancelled
from mindspace_graph.models import (
    ChatResponse,
    JsonUpdatePlan,
    JsonUpdateValidation,
    JsonWriteReceipt,
    ModelDiagnostics,
    ProtocolOutput,
    ProviderHttpAttempt,
    RoleValidation,
)
from mindspace_graph.native_tools import (
    native_call_to_instruction,
    native_tool_choice,
    native_tool_definitions,
    supports_native_tools,
)
from mindspace_graph.policies import rank_with_temporal_decay
from mindspace_graph.ports import Dependencies
from mindspace_graph.profile_bootstrap import evaluate_profile_bootstrap
from mindspace_graph.prompting import build_prompt, resolve_initiative_request
from mindspace_graph.protocol import IncrementalResponseParser, ProtocolParser
from mindspace_graph.r18_director import explicit_r18_requested
from mindspace_graph.roleplay import (
    allow_raw_chat_retrieval,
    normalize_presentation_response,
    normalize_voice_response,
    resolve_presentation_mode,
)
from mindspace_graph.state import TurnState
from mindspace_graph.tool_chain import (
    FINAL_AFTER_TOOL_PROTOCOL,
    ToolExecutionResult,
    enforce_tool_claims,
    execute_memory_tool,
    failed_result,
    parse_task_review,
    task_review_messages,
    tool_result_json,
)
from mindspace_graph.voice_render import VoiceCueStream, extract_voice_cue, normalize_voice_cue

_CONTEXTUAL_RECALL = re.compile(
    r"(?:刚刚|刚才|之前|上次|以前|记得|忘了|有没有|是否|真的?没|第一次|"
    r"还是|那个|这件事|这样|那样|什么味道|怎么回事)",
    re.IGNORECASE,
)
_EXPLICIT_ADULT_RECALL = re.compile(
    r"(?:性交|做爱|口交|手交|插入|抽送|射(?:精|了|的)|内射|高潮|阴茎|阴道|"
    r"精液|鸡巴|肉棒|小穴|跳蛋|潮吹)",
    re.IGNORECASE,
)
_ADULT_FACT_TOKENS = (
    "性交",
    "做爱",
    "口交",
    "手交",
    "插入",
    "抽送",
    "射",
    "内射",
    "高潮",
    "精液",
    "跳蛋",
    "潮吹",
    "吞",
    "舔",
)


def build_contextual_retrieval_query(
    message: str, history: list[dict[str, Any]], *, limit: int = 900
) -> tuple[str, str]:
    """Resolve short/anaphoric chat queries with nearby dialogue, without an LLM call."""

    current = re.sub(r"\s+", " ", message or "").strip()
    compact = re.sub(r"\s+", "", current)
    if len(compact) > 24 and not _CONTEXTUAL_RECALL.search(compact):
        return current, "current_only"
    anchors: list[str] = []
    for item in reversed(history):
        if item.get("hidden") or item.get("role") not in {"user", "assistant"}:
            continue
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if not content:
            continue
        anchors.append(content[:240])
        if len(anchors) >= 4:
            break
    if not anchors:
        return current, "current_only"
    query = "\n".join([current, "相关近因：", *reversed(anchors)])
    return query[-limit:], "anaphora_expanded"


def should_open_adult_continuity(message: str, history: list[dict[str, Any]], *, adult_mode: bool) -> bool:
    """Allow adult history only for an explicit topic or its immediate anaphoric follow-up."""

    if adult_mode or _EXPLICIT_ADULT_RECALL.search(message or ""):
        return True
    if not _CONTEXTUAL_RECALL.search(message or ""):
        return False
    visible = [
        str(item.get("content") or "")
        for item in history
        if not item.get("hidden") and item.get("role") in {"user", "assistant"}
    ]
    return any(_EXPLICIT_ADULT_RECALL.search(text) for text in visible[-4:])


def _adult_fact_overlap(query: str, text: str) -> int:
    query_tokens = {token for token in _ADULT_FACT_TOKENS if token in query}
    return sum(token in text for token in query_tokens)


class NodeFactory:
    """LangGraph 节点实现。

    节点只返回状态增量；除 persist_turn 外不提交会话、档案或结构化记忆。
    StreamWriter 只负责发诊断/SSE 事件，不应被当成业务状态存储。
    """

    def __init__(self, dependencies: Dependencies):
        self.deps = dependencies
        self.parser = ProtocolParser()

    CALL_BUDGETS = {
        "generation": 1,
        "task_review": 1,
        "final_generation": 1,
    }
    TOTAL_CALL_BUDGET = 3

    @classmethod
    def _call_allowed(cls, state: TurnState, kind: str) -> bool:
        """Apply per-purpose and total limits without coupling unrelated model calls."""

        counts = state.get("llm_call_counts", {})
        total = sum(int(value) for value in counts.values())
        return total < cls.TOTAL_CALL_BUDGET and int(counts.get(kind, 0)) < int(cls.CALL_BUDGETS[kind])

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
        attempts = state.get("provider_attempts", [])
        return ModelDiagnostics(
            call_summary=state.get("model_call_summary", []),
            total_calls=state.get("llm_call_count", 0),
            provider_attempts=attempts,
            total_http_attempts=len(attempts),
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
            "provider_attempts": [],
            "trace": ["validate_request"],
        }

    def load_context(self, state: TurnState) -> dict[str, Any]:
        """读取本轮一致性快照，不调用模型，也不无条件采集本机状态。"""

        self._check_cancelled(state)
        request = state["request"]
        profiles = self.deps.profiles.load_bundle(request.character_id)
        request = resolve_initiative_request(request, profiles)
        deletion_events = self.deps.sessions.load_pending_deletions(request.session_id)
        recent_history = list(state.get("session_snapshot") or self.deps.sessions.load_all(request.session_id))
        if request.mode == "regenerate":
            recent_history = [item for item in recent_history if int(item.get("round", 0)) != request.round]
            if self.deps.context is not None:
                self.deps.context.invalidate(
                    request.session_id,
                    reason="round_regenerated",
                    details={"round": request.round},
                )
        previous_emotion_state = None
        if request.interaction_mode == "voice" and self.deps.emotion is not None and self.deps.emotion.enabled():
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

    def retrieve_chat(self, state: TurnState) -> dict[str, Any]:
        """当前会话与结构化记忆召回分支；不重复执行知识库召回。"""

        self._check_cancelled(state)
        request = state["request"]
        settings = request.retrieval
        chunks = []
        query = request.message
        query_mode = "current_only"
        if not settings.ready:
            return {
                "chat_chunks": [],
                "trace": ["retrieve_chat_deferred"],
            }
        capability_allowed = self.deps.capabilities is None or self.deps.capabilities.enabled("local_knowledge_enabled")
        if capability_allowed and settings.rag_enabled and settings.chat_enabled:
            query, query_mode = build_contextual_retrieval_query(request.message, state.get("recent_history", []))
            adult_recall = should_open_adult_continuity(
                request.message,
                state.get("recent_history", []),
                adult_mode=request.adult_mode,
            )
            chunks = self.deps.retriever.search_chat(
                query,
                request.session_id,
                (settings.chat_k + settings.history_k) * settings.candidate_multiplier,
                character_id=request.character_id,
                settings=settings,
                user_name=request.user_name,
                character_name=request.character_name,
                messages=state.get("recent_history", []),
                include_raw_chat=allow_raw_chat_retrieval(request),
                adult_mode=adult_recall,
            )
            chunks = [
                item for item in chunks
                if item.source == "chat" and item.score >= settings.similarity_threshold
            ]
        return {
            "chat_chunks": chunks,
            "retrieval_query": query if settings.ready else request.message,
            "retrieval_query_mode": query_mode if settings.ready else "deferred",
            "adult_recall_opened": bool(
                settings.ready
                and not request.adult_mode
                and should_open_adult_continuity(
                    request.message,
                    state.get("recent_history", []),
                    adult_mode=False,
                )
            ),
            "trace": ["retrieve_chat"],
        }

    def rank_context(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        combined = state.get("chat_chunks", [])
        request = state["request"]
        quotas = {
            "knowledge": request.retrieval.knowledge_k,
            "chat": request.retrieval.chat_k,
            "memory": request.retrieval.history_k,
        }
        ranked: list[Any] = []
        # Source quotas are upper bounds, not a demand to fill the prompt with
        # irrelevant material.  The order is also the prompt order: external
        # reference, raw dialogue evidence, then concise structured history.
        for source in ("knowledge", "chat", "memory"):
            source_chunks = [item for item in combined if item.source == source]
            source_ranked = rank_with_temporal_decay(source_chunks, request, limit=quotas[source])
            if source == "chat" and state.get("adult_recall_opened"):
                query = str(state.get("retrieval_query") or request.message)
                anchors = sorted(
                    (
                        item
                        for item in source_chunks
                        if bool(item.metadata.get("adult_mode")) and _adult_fact_overlap(query, item.text) > 0
                    ),
                    key=lambda item: (
                        _adult_fact_overlap(query, item.text),
                        item.round_num,
                        item.score,
                    ),
                    reverse=True,
                )[: min(2, max(0, quotas[source] - 1))]
                anchor_ids = {item.chunk_id for item in anchors}
                source_ranked = [
                    *anchors,
                    *(item for item in source_ranked if item.chunk_id not in anchor_ids),
                ][: quotas[source]]
            ranked.extend(source_ranked)
        if state.get("adult_recall_opened"):
            ranked = [
                item.model_copy(
                    update={
                        "metadata": {
                            **item.metadata,
                            "adult_continuity_access": True,
                        }
                    }
                )
                for item in ranked
            ]
        self.deps.retriever.record_retrieval(combined, ranked, request.round)
        writer(
            {
                "event": "retrieval.completed",
                "data": {
                    "knowledge": 0,
                    "chat": len(state.get("chat_chunks", [])),
                    "selected_counts": {
                        "knowledge": sum(item.source == "knowledge" for item in ranked),
                        "dialogue": sum(item.source == "chat" for item in ranked),
                        "history": sum(item.source == "memory" for item in ranked),
                    },
                    "ranked": [item.model_dump(mode="json") for item in ranked],
                    "query_mode": state.get("retrieval_query_mode", "current_only"),
                    "query_characters": len(state.get("retrieval_query", request.message)),
                    "adult_recall_opened": bool(state.get("adult_recall_opened")),
                    "ready": request.retrieval.ready,
                    "deferred_reason": request.retrieval.deferred_reason,
                },
            }
        )
        trace = "rank_context" if request.retrieval.ready else "rank_context_deferred"
        return {"ranked_context": ranked, "trace": [trace]}

    def tool_hint(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        """Produce a zero-call hint only; the model remains the tool selector."""

        self._check_cancelled(state)
        service = self.deps.capabilities
        hint = service.route_hint(state["request"], history=state.get("recent_history", [])) if service else ""
        writer({"event": "tool.hinted", "data": {"hint": hint, "model_calls": 0}})
        return {"tool_hint": hint, "trace": ["tool_hint"]}

    @staticmethod
    def route_tool_request(state: TurnState) -> str:
        return "tool" if state.get("tool_instruction") is not None else "answer"

    def authorize_tool(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        instruction = state["tool_instruction"]
        service = self.deps.capabilities
        error = ""
        # The legacy switch controls autonomous/background web use. A web hint is
        # produced only when the current user turn itself asks for, or clearly
        # requires, current external information; that explicit L3 request is a
        # one-turn authorization and must not be silently rejected by stale
        # desktop settings carried forward from the pre-0.8.2 capability model.
        user_authorized_web = instruction.tool == "web" and state.get("tool_hint") == "web"
        if instruction.tool == "web" and (
            service is None
            or (not service.enabled("web_search_enabled") and not user_authorized_web)
        ):
            error = "web tool is disabled"
        elif instruction.tool == "memory" and (service is None or not service.enabled("local_knowledge_enabled")):
            error = "memory tool is disabled"
        elif instruction.tool == "task" and self.deps.characters is None:
            error = "task repository is unavailable"
        if not error:
            return {"trace": ["authorize_tool"]}
        result = failed_result(instruction, "denied", error)
        writer({"event": "tool.failed", "data": result.model_dump(mode="json")})
        return {"tool_result": result, "trace": ["authorize_tool"]}

    @staticmethod
    def route_tool_authorization(state: TurnState) -> str:
        if state.get("tool_result") is not None:
            return "inject"
        return "task" if state["tool_instruction"].tool == "task" else "execute"

    def review_task(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        instruction = state["tool_instruction"]
        if not self._call_allowed(state, "task_review"):
            result = failed_result(instruction, "denied", "task review call budget exhausted")
            return {"tool_result": result, "task_review_allowed": False, "trace": ["review_task"]}
        started = time.perf_counter()
        allowed = False
        reason = ""
        usages = list(state.get("model_usage", []))
        try:
            config = state["request"].api.model_copy(update={"temperature": 0, "max_tokens": 160})
            raw = self.deps.llm.generate(
                task_review_messages(instruction, state["request"].message),
                config,
            )
            allowed, reason = parse_task_review(raw)
            usage = self._take_model_usage(writer)
            if usage is not None:
                usages.append(usage)
        except Exception as exc:
            reason = str(exc)[:500]
        writer(
            {
                "event": "tool.reviewed",
                "data": {
                    "call_id": instruction.call_id,
                    "tool": "task",
                    "level": 2,
                    "allowed": allowed,
                    "reason": reason,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            }
        )
        update: dict[str, Any] = {
            "task_review_allowed": allowed,
            "task_review_reason": reason,
            "model_usage": usages,
            "provider_attempts": [
                *state.get("provider_attempts", []),
                *self._take_provider_attempts(writer),
            ],
            "trace": ["review_task"],
        }
        if not allowed:
            update["tool_result"] = failed_result(instruction, "denied", reason or "task review denied")
        update.update(self._record_call(state, "task_review", started, status="success" if allowed else "denied"))
        return update

    @staticmethod
    def route_task_review(state: TurnState) -> str:
        return "execute" if state.get("task_review_allowed") else "inject"

    def execute_tool(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        instruction = state["tool_instruction"]
        started = time.perf_counter()
        writer(
            {
                "event": "tool.started",
                "data": {
                    "call_id": instruction.call_id,
                    "tool": instruction.tool,
                    "level": instruction.level,
                    "parameter_summary": instruction.parameter_summary,
                },
            }
        )
        try:
            if instruction.tool == "web":
                result = self.deps.capabilities.execute_web(instruction)
            elif instruction.tool == "memory":
                result = execute_memory_tool(
                    instruction,
                    request=state["request"],
                    state=state,
                    deps=self.deps,
                )
            else:
                receipt = self.deps.characters.execute_task_command(
                    state["request"].character_id,
                    instruction.command or {},
                    request_id=state.get("request_id", ""),
                    command_hash=instruction.command_hash,
                    expected_revision=int(state["profiles"].revisions.get("character_memory", 0)),
                )
                result = ToolExecutionResult(
                    call_id=instruction.call_id,
                    tool="task",
                    level=2,
                    status="success",
                    parameter_summary=instruction.parameter_summary,
                    source_count=int(receipt.get("count", 0)),
                    data={"tasks": receipt.get("tasks", []), "count": receipt.get("count", 0)},
                    receipt=receipt,
                )
        except Exception as exc:
            result = failed_result(instruction, "failed", str(exc))
        if not result.elapsed_ms:
            result = result.model_copy(update={"elapsed_ms": round((time.perf_counter() - started) * 1000, 1)})
        event = "tool.completed" if result.status == "success" else "tool.failed"
        writer({"event": event, "data": result.model_dump(mode="json")})
        self.deps.audit.record(
            event.replace(".", "_"),
            {
                "request_id": state.get("request_id", ""),
                "session_id": state["request"].session_id,
                **result.model_dump(mode="json", exclude={"data"}),
            },
        )
        return {"tool_result": result, "trace": ["execute_tool"]}

    def inject_tool_result(self, state: TurnState) -> dict[str, Any]:
        result = state["tool_result"]
        native_call = state.get("native_tool_call")
        if not native_call:
            raise RuntimeError("native tool result is missing its provider call")
        messages = [
            *state["prompt_messages"],
            {"role": "assistant", "content": "", "tool_calls": [native_call]},
            {
                "role": "tool",
                "tool_call_id": native_call.get("id", ""),
                "content": tool_result_json(result),
            },
            {"role": "system", "content": FINAL_AFTER_TOOL_PROTOCOL},
        ]
        if self.deps.prompt_inspector is not None:
            self.deps.prompt_inspector.record(
                run_id=state.get("request_id", ""),
                session_id=state["request"].session_id,
                character_id=state["request"].character_id,
                messages=messages,
                pending_events=state.get("prompt_pending_events", []),
            )
        return {"prompt_messages": messages, "trace": ["inject_result"]}

    def generate_final(self, state: TurnState, writer: StreamWriter) -> dict[str, Any]:
        self._check_cancelled(state)
        if not self._call_allowed(state, "final_generation"):
            raise RuntimeError("final generation model call budget exhausted")
        request = state["request"]
        # The first call may intentionally cap a short user message at 200
        # tokens. Once a tool has returned multiple sources, that short-turn
        # cap is no longer an appropriate answer budget.
        final_api = request.api.model_copy(
            update={"max_tokens": max(int(request.api.max_tokens), 1200)}
        )
        started = time.perf_counter()
        extractor = IncrementalResponseParser()
        chunks: list[str] = []
        attempts: list[ProviderHttpAttempt] = []
        try:
            for token in self.deps.llm.stream(state["prompt_messages"], final_api):
                self._check_cancelled(state)
                chunks.append(token)
                for delta in extractor.feed(token):
                    normalized = normalize_voice_response(delta, request)
                    if normalized:
                        writer({"event": "response.delta", "data": {"delta": normalized}})
        finally:
            attempts = self._take_provider_attempts(writer)
        usage = self._take_model_usage(writer)
        return {
            "raw_candidate": "".join(chunks),
            "model_usage": [*state.get("model_usage", []), *([usage] if usage else [])],
            "provider_attempts": [*state.get("provider_attempts", []), *attempts],
            **self._record_call(state, "final_generation", started),
            "trace": ["generate_final"],
        }
    def compose_prompt(self, state: TurnState) -> dict[str, Any]:
        """把权威数据、账本历史和本轮临时上下文组装成主模型 messages。"""

        self._check_cancelled(state)
        native_tools_enabled = supports_native_tools(state["request"].api.base_url) and callable(
            getattr(self.deps.llm, "stream_with_tools", None)
        )
        built = build_prompt(
            state["request"],
            state["profiles"],
            state.get("recent_history", []),
            state.get("ranked_context", []),
            state.get("deletion_events", []),
            state.get("profile_bootstrap"),
            state.get("tool_hint", ""),
            state.get("emotion_state"),
            context_ledger=self.deps.context,
            native_tools_enabled=native_tools_enabled,
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
            "native_tools_enabled": native_tools_enabled,
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
        defer_visible_response = explicit_r18_requested(request)
        started = time.perf_counter()
        extractor = IncrementalResponseParser()
        # Retain backward-compatible cue parsing for older model templates.
        # The Base character voice no longer requires a leading cue.
        voice_cue_stream = VoiceCueStream(allow_adult=request.adult_mode)
        active_tts_provider = self.deps.tts_provider() if callable(self.deps.tts_provider) else self.deps.tts_provider
        emit_voice_cue = request.interaction_mode == "voice" and active_tts_provider == "qwen3-vllm"
        voice_cue_sent = False
        chunks: list[str] = []
        if state.get("native_tools_enabled"):
            hint = state.get("tool_hint", "")
            token_stream = self.deps.llm.stream_with_tools(
                state["prompt_messages"],
                request.api,
                tools=native_tool_definitions(hint),
                tool_choice=native_tool_choice(hint),
            )
        else:
            token_stream = self.deps.llm.stream(state["prompt_messages"], request.api)
        attempts: list[ProviderHttpAttempt] = []
        try:
            for token in token_stream:
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
        finally:
            attempts = self._take_provider_attempts(writer)
        if not defer_visible_response:
            for delta in voice_cue_stream.flush():
                if emit_voice_cue and not voice_cue_sent:
                    writer({"event": "response.voice_cue", "data": {"cue": voice_cue_stream.cue}})
                    voice_cue_sent = True
                normalized = normalize_voice_response(delta, request)
                if normalized:
                    writer({"event": "response.delta", "data": {"delta": normalized}})
        raw = "".join(chunks)
        native_call = None
        instruction = None
        if state.get("native_tools_enabled"):
            native_call = self.deps.llm.take_native_tool_call()
            if native_call:
                instruction = native_call_to_instruction(native_call, user_message=request.message)
                writer(
                    {
                        "event": "tool.requested",
                        "data": {
                            "call_id": instruction.call_id,
                            "tool": instruction.tool,
                            "level": instruction.level,
                            "parameter_summary": instruction.parameter_summary,
                        },
                    }
                )
        usage = self._take_model_usage(writer)
        self._check_cancelled(state)
        return {
            "raw_candidate": raw,
            "voice_cue": voice_cue_stream.cue,
            "voice_cue_event_sent": voice_cue_sent,
            **({"native_tool_call": native_call} if native_call else {}),
            **({"tool_instruction": instruction} if instruction else {}),
            "model_usage": [*state.get("model_usage", []), *([usage] if usage else [])],
            "provider_attempts": [*state.get("provider_attempts", []), *attempts],
            **self._record_call(state, "generation", started),
            "trace": ["generate_candidate"],
        }

    def _take_model_usage(self, writer: StreamWriter):
        take_usage = getattr(self.deps.llm, "take_usage", None)
        usage = take_usage() if callable(take_usage) else None
        if usage is not None:
            writer({"event": "model.usage", "data": usage.model_dump(mode="json")})
        return usage

    def _take_provider_attempts(self, writer: StreamWriter) -> list[ProviderHttpAttempt]:
        take_attempts = getattr(self.deps.llm, "take_provider_attempts", None)
        raw_attempts = take_attempts() if callable(take_attempts) else []
        attempts = [ProviderHttpAttempt.model_validate(item) for item in (raw_attempts or [])]
        for attempt in attempts:
            payload = attempt.model_dump(mode="json")
            writer({"event": "model.attempt", "data": payload})
            self.deps.audit.record("model_attempt", payload)
        return attempts

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
        voice_cue = (
            parsed_voice_cue
            if explicit_cue
            else normalize_voice_cue(state.get("voice_cue"), allow_adult=request.adult_mode)
        )
        active_tts_provider = self.deps.tts_provider() if callable(self.deps.tts_provider) else self.deps.tts_provider
        emit_voice_cue = request.interaction_mode == "voice" and active_tts_provider == "qwen3-vllm"
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
                        "model_json_update_ignored" if parsed_protocol is not None else "visible_response_recovered"
                    ),
                },
            )
            errors = []
        update: dict[str, Any] = {
            "protocol_errors": errors,
            "voice_cue": voice_cue,
            "voice_cue_event_sent": bool(emit_voice_cue or state.get("voice_cue_event_sent")),
            "trace": ["parse_protocol"],
        }
        if visible_response:
            update["fallback_response"] = visible_response
        if protocol is not None:
            normalized_response = normalize_presentation_response(
                protocol.response,
                request,
                state.get("recent_history", []),
            )
            if normalized_response != protocol.response:
                protocol = protocol.model_copy(update={"response": normalized_response})
                update["fallback_response"] = normalized_response
                writer(
                    {
                        "event": "response.replace",
                        "data": {
                            "content": normalized_response,
                            "reason": "presentation_boundary",
                        },
                    }
                )
            guarded_response, capability_violations = enforce_tool_claims(
                protocol.response,
                state.get("tool_result"),
                tool_hint=state.get("tool_hint", ""),
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
                            "reason": "unverified_tool_claim_blocked",
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
            self.deps.context.find_turn_commit(state.get("request_id", "")) if self.deps.context is not None else None
        )
        if existing_commit is not None:
            if (
                str(existing_commit["session_id"]) != request.session_id
                or int(existing_commit["round_num"]) != request.round
            ):
                raise ValueError("request_id already belongs to another turn")
            assistant_id = str(existing_commit["assistant_message_id"])
            existing_message = next(
                (item for item in state.get("recent_history", []) if item.get("message_id") == assistant_id),
                None,
            )
            reply = str(existing_message.get("content") or protocol.response) if existing_message else protocol.response
            response = ChatResponse(
                session_id=request.session_id,
                round=request.round,
                status="success",
                reply=reply,
                assistant_message_id=assistant_id,
                presentation_mode=resolve_presentation_mode(request, state.get("recent_history", [])),
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
        if request.mode == "regenerate":
            if self.deps.memory is not None:
                self.deps.memory.forget_session(request.session_id, request.round)
            if self.deps.context is not None:
                self.deps.context.replace_round(request.session_id, request.round)
        persisted = self.deps.sessions.persist_turn(
            request,
            protocol.response,
            replace_round=request.mode == "regenerate",
            write_receipt=receipt,
            tool_execution=(state["tool_result"].model_dump(mode="json") if state.get("tool_result") else None),
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
                    dict(item) for item in state.get("prompt_pending_events", []) if not item.get("ephemeral")
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
                    character_id=request.character_id,
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
        if primary_commit_allowed and validation.is_valid and deletion_events and deletion_decision_complete:
            resolved_ids = [event.event_id for event in deletion_events]
            self.deps.sessions.resolve_deletions(resolved_ids)

        response = ChatResponse(
            session_id=request.session_id,
            round=request.round,
            status="success",
            reply=protocol.response,
            assistant_message_id=persisted["assistant_message_id"],
            presentation_mode=resolve_presentation_mode(request, state.get("recent_history", [])),
            writeback_applied=receipt.applied,
            retrieval_counts={
                "knowledge": sum(item.source == "knowledge" for item in state.get("ranked_context", [])),
                "chat": sum(item.source == "chat" for item in state.get("ranked_context", [])),
                "history": sum(item.source == "memory" for item in state.get("ranked_context", [])),
            },
            tool_execution=(state["tool_result"].model_dump(mode="json") if state.get("tool_result") else None),
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
        role = state.get("role_validation") or RoleValidation(is_valid=False, message="role validation not reached")
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

