"""Conversation application service and durable streaming orchestration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from mindspace_graph.cancellation import CancellationRegistry, GenerationCancelled
from mindspace_graph.compaction import ContextCompactionService
from mindspace_graph.conversation_runs import (
    BufferedStreamRun,
    ConversationRunRepository,
    StreamEnvelopeFactory,
)
from mindspace_graph.event_memory import EventMemoryWritebackService
from mindspace_graph.graph import build_graph
from mindspace_graph.memory_writeback import MemoryWritebackService
from mindspace_graph.models import (
    ApiConfig,
    ChatRequest,
    ChatResponse,
)
from mindspace_graph.ports import Dependencies
from mindspace_graph.role_audit import RoleAuditService
from mindspace_graph.role_runtime import build_runtime_role_state
from mindspace_graph.settings import AppSettings
from mindspace_graph.application.retrieval_warmup import RetrievalWarmupCoordinator
from mindspace_graph.application.turn_preparation import TurnPreparationService

NODE_LABELS = {
    "validate_request": "校验请求",
    "load_context": "加载会话与档案",
    "retrieve_chat": "检索会话记忆",
    "rank_context": "重排上下文",
    "tool_hint": "生成零调用工具提示",
    "compose_prompt": "构建上下文",
    "generate_candidate": "生成回复",
    "authorize_tool": "授权工具",
    "review_task": "审查任务操作",
    "execute_tool": "执行工具",
    "inject_result": "注入工具结果",
    "generate_final": "生成最终回复",
    "parse_protocol": "解析协议",
    "validate_role": "校验角色一致性",
    "validate_json_update": "校验 JSON 小幅更新",
    "persist_turn": "保存本轮对话",
    "finalize_error": "整理错误",
}
class ConversationService:
    """对话图的进程级外壳：注入服务端配置、管理流恢复并调度后台任务。"""

    def __init__(
        self,
        settings: AppSettings,
        dependencies: Dependencies,
        cancellation: CancellationRegistry,
    ) -> None:
        self.settings = settings
        self.dependencies = dependencies
        self.cancellation = cancellation
        self.graph = build_graph(dependencies)
        if dependencies.context is None:
            raise ValueError("ConversationService requires a context ledger")
        self.compaction = ContextCompactionService(
            settings=settings,
            ledger=dependencies.context,
            profiles=dependencies.profiles,
            llm_provider=lambda: self.dependencies.llm,
            active_run_count=cancellation.active_count,
            character_for_session=lambda session_id: str(
                self.dependencies.sessions.load_session(session_id).get("character_id") or ""
            ),
        )
        self.role_audit = RoleAuditService(
            ledger=dependencies.context,
            llm_provider=lambda: self.dependencies.llm,
            api_provider=self._role_audit_api,
            active_run_count=cancellation.active_count,
            enabled=lambda: self.settings.role_audit_enabled,
        )
        self.memory_writeback = MemoryWritebackService(
            dependencies=self.dependencies,
            api_provider=self._memory_writeback_api,
        )
        if dependencies.event_memory is None:
            raise ValueError("ConversationService requires event memory")
        self.event_memory_writeback = EventMemoryWritebackService(
            dependencies=self.dependencies,
            store=dependencies.event_memory,
            api_provider=self._memory_writeback_api,
        )
        self._run_repository = ConversationRunRepository(dependencies.database)
        # Compatibility views retained for existing diagnostics and tests.
        self._stream_runs = self._run_repository.active_runs
        self._stream_runs_lock = self._run_repository.lock
        self.retrieval_warmup = RetrievalWarmupCoordinator(dependencies)
        self.turn_preparation = TurnPreparationService(
            settings,
            dependencies,
            retrieval_is_ready=self.retrieval_warmup.is_ready,
        )

    def _role_audit_api(self) -> ApiConfig:
        return ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.role_audit_model or self.settings.llm_model,
            temperature=0,
            max_tokens=600,
        )

    def _memory_writeback_api(self) -> ApiConfig:
        return ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            temperature=0,
            max_tokens=700,
        )

    def refresh_language_model(self) -> None:
        factory = self.dependencies.language_model_factory
        if factory is None:
            raise RuntimeError("ConversationService requires a language model factory to refresh the model")
        close = getattr(self.dependencies.llm, "close", None)
        if callable(close):
            close()
        self.dependencies.llm = factory.create()
        if self.dependencies.context is not None:
            self.dependencies.context.configure_hard_limit(
                context_window=self.settings.llm_context_window,
                hard_ratio=self.settings.context_compaction_hard_ratio,
                reserved_tokens=self.settings.context_compaction_max_tokens,
            )

    def close(self) -> None:
        self.memory_writeback.close()
        self.event_memory_writeback.close()
        self.retrieval_warmup.close()
        for resource in (
            self.dependencies.llm,
            self.dependencies.capabilities,
            self.dependencies.emotion,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    async def aclose(self) -> None:
        await self.memory_writeback.drain()
        await self.event_memory_writeback.drain()
        tasks = [run.task for run in self._stream_runs.values() if run.task is not None and not run.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(
                *tasks,
                self.retrieval_warmup.aclose(),
                return_exceptions=True,
            )
        else:
            await self.retrieval_warmup.aclose()
        self.close()

    def session_role_state(self, character_id: str, *, request_user_name: str = "") -> dict[str, Any]:
        profiles = self.dependencies.profiles.load_bundle(character_id)
        return build_runtime_role_state(
            ai_profile=profiles.ai_profile,
            character_memory=profiles.character_memory,
            user_profile=profiles.user_profile,
            request_user_name=request_user_name,
        )

    def _server_request(
        self,
        request: ChatRequest,
        session_snapshot: list[dict[str, Any]] | None = None,
    ) -> ChatRequest:
        """Compatibility delegate for the authoritative turn preparation service."""

        return self.turn_preparation.prepare(request, session_snapshot)

    async def invoke(self, request: ChatRequest, request_id: str | None = None) -> ChatResponse:
        """Run or join the same durable execution used by the SSE endpoint."""

        request_id = request_id or uuid4().hex
        run = await self._ensure_stream_run(request, request_id)
        if run.task is not None:
            await asyncio.shield(run.task)
        if run.response is not None:
            return run.response
        raise RuntimeError(run.error or f"durable run ended without a response: {run.terminal_event or 'unknown'}")

    async def stream(
        self,
        request: ChatRequest,
        request_id: str | None = None,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[str]:
        """订阅一个可恢复运行；相同 request_id 不会重复启动同一轮图。"""

        request_id = request_id or uuid4().hex
        run = await self._ensure_stream_run(request, request_id)
        async for event in self._subscribe_stream(run, after_sequence):
            yield event

    async def prepare_stream(self, request: ChatRequest, request_id: str | None = None) -> str:
        """Create or validate a stream run before HTTP commits an SSE response."""

        resolved_request_id = request_id or uuid4().hex
        await self._ensure_stream_run(request, resolved_request_id)
        return resolved_request_id

    async def resume_stream(self, request_id: str, *, after_sequence: int = 0) -> AsyncIterator[str]:
        async for event in self._run_repository.resume(request_id, after_sequence=after_sequence):
            yield event

    async def stream_status(self, request_id: str) -> dict[str, Any] | None:
        return await self._run_repository.status(request_id)

    async def _ensure_stream_run(self, request: ChatRequest, request_id: str) -> BufferedStreamRun:
        """创建或复用运行，并拒绝 request_id 被绑定到另一轮。"""

        return await self._run_repository.ensure(request, request_id, self._produce_stream)

    async def _publish_stream(self, run: BufferedStreamRun, sequence: int, payload: str, *, terminal: str = "") -> None:
        await self._run_repository.publish(run, sequence, payload, terminal=terminal)

    async def _subscribe_stream(self, run: BufferedStreamRun, after_sequence: int) -> AsyncIterator[str]:
        """从 after_sequence 后重放，再跟随新事件直到终态。"""

        async for event in self._run_repository.subscribe(run, after_sequence):
            yield event

    async def _produce_stream(self, run: BufferedStreamRun) -> None:
        """只启动一次 LangGraph，把 tasks/updates/custom 统一封装成有序 SSE。"""

        request = run.request
        request_id = run.request_id
        events = StreamEnvelopeFactory(request_id, request.session_id, request.round)
        self.cancellation.start(request_id)
        accepted = events.sse(
            "run.accepted",
            {
                "request_id": request_id,
                "session_id": request.session_id,
                "round": request.round,
            },
        )
        await self._publish_stream(run, events.sequence, accepted)
        final: ChatResponse | None = None
        run_finished = False
        try:
            # Resolve server-owned character/provider state inside the guarded
            # section.  Archived or missing characters must become a terminal
            # run.error event instead of leaving subscribers after run.accepted
            # with a permanently running durable record.
            await self.memory_writeback.flush_session(request.session_id)
            await self.event_memory_writeback.flush_session(request.session_id)
            session_snapshot = self.dependencies.sessions.load_all(request.session_id)
            server_request = self._server_request(request, session_snapshot)
            async for part in self.graph.astream(
                {
                    "request": server_request,
                    "request_id": request_id,
                    "session_snapshot": session_snapshot,
                },
                config={"recursion_limit": 20},
                stream_mode=["tasks", "updates", "custom"],
                version="v2",
            ):
                part_type = part.get("type")
                data = part.get("data")
                if part_type == "tasks" and isinstance(data, dict):
                    node = str(data.get("name") or "unknown")
                    started = "input" in data and "result" not in data
                    payload = events.sse(
                        "node.started" if started else "node.completed",
                        {
                            "node": node,
                            "label": NODE_LABELS.get(node, node),
                            "error": data.get("error") if not started else None,
                        },
                    )
                    await self._publish_stream(run, events.sequence, payload)
                elif part_type == "custom" and isinstance(data, dict):
                    event = str(data.get("event") or "graph.custom")
                    payload = data.get("data")
                    event_data = payload if isinstance(payload, dict) else {"value": payload}
                    payload = events.sse(event, event_data)
                    await self._publish_stream(run, events.sequence, payload)
                elif part_type == "updates" and isinstance(data, dict):
                    for _node, values in data.items():
                        if isinstance(values, dict) and isinstance(values.get("response"), ChatResponse):
                            final = values["response"]
            if final is None:
                run.error = "missing final response"
                payload = events.sse("run.error", {"error": "missing final response"})
                await self._publish_stream(run, events.sequence, payload, terminal="run.error")
            elif final.status == "error":
                run.response = final
                payload = events.sse("run.error", {"response": final.model_dump(mode="json")})
                await self._publish_stream(run, events.sequence, payload, terminal="run.error")
            else:
                run.response = final
                self.cancellation.finish(request_id)
                run_finished = True
                self.retrieval_warmup.kick(server_request)
                self.memory_writeback.kick(server_request, final)
                self.event_memory_writeback.kick(server_request, final)
                self.compaction.kick()
                self.role_audit.kick()
                payload = events.sse("run.completed", {"response": final.model_dump(mode="json")})
                await self._publish_stream(run, events.sequence, payload, terminal="run.completed")
        except GenerationCancelled:
            run.error = "generation cancelled"
            payload = events.sse("run.cancelled", {"cancelled": True})
            await self._publish_stream(run, events.sequence, payload, terminal="run.cancelled")
        except asyncio.CancelledError:
            # Graceful Core shutdown preserves the partial answer and exposes a
            # stable interrupted terminal instead of pretending generation failed.
            payload = events.sse(
                "run.interrupted",
                {"partial_text": run.partial_text, "reason": "core_shutdown"},
            )
            await self._publish_stream(run, events.sequence, payload, terminal="run.interrupted")
            run.error = "core shutdown interrupted the run"
            raise
        except Exception as exc:  # noqa: BLE001 - converted to a stable API event
            run.error = str(exc)
            self.dependencies.audit.record("stream_failed", {"request_id": request_id, "error": str(exc)})
            payload = events.sse(
                "run.error",
                {
                    "error": str(exc),
                    "model": {
                        "provider_attempts": run.provider_attempts,
                        "total_http_attempts": len(run.provider_attempts),
                    },
                },
            )
            await self._publish_stream(run, events.sequence, payload, terminal="run.error")
        finally:
            if not run_finished:
                self.cancellation.finish(request_id)
                self.compaction.kick()
                self.role_audit.kick()
            if not run.completed:
                payload = events.sse("run.error", {"error": "stream ended unexpectedly"})
                await self._publish_stream(run, events.sequence, payload, terminal="run.error")

    def interrupt(self, request_id: str) -> bool:
        return self.cancellation.cancel(request_id)

__all__ = ["ConversationService"]
