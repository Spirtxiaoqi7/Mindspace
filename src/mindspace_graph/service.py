"""Application service coordinating the graph, persistence, and cancellation."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mindspace_graph.adapters.file_storage import JsonProfileRepository, JsonSessionRepository
from mindspace_graph.adapters.in_memory import DeterministicLanguageModel, RegexRolePolicy
from mindspace_graph.adapters.json_audit import JsonlAudit
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever
from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.asr_vocabulary import ASRVocabularyStore
from mindspace_graph.cancellation import CancellationRegistry, GenerationCancelled
from mindspace_graph.capabilities import ReadOnlyCapabilityService
from mindspace_graph.compaction import ContextCompactionService
from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.emotion_disabled import DisabledEmotionCoordinator
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.graph import build_graph
from mindspace_graph.memory_service import StructuredMemoryService
from mindspace_graph.models import ApiConfig, ChatRequest, ChatResponse
from mindspace_graph.ports import Dependencies
from mindspace_graph.product_config import ProductConfigStore
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.prompt_inspection import PromptInspectionStore
from mindspace_graph.role_audit import RoleAuditService
from mindspace_graph.settings import AppSettings

NODE_LABELS = {
    "validate_request": "校验请求",
    "load_context": "加载会话与档案",
    "capture_local_snapshot": "采集本机只读状态",
    "retrieve_knowledge": "检索知识库",
    "retrieve_chat": "检索会话记忆",
    "rank_context": "重排上下文",
    "capability_route": "判断只读能力",
    "plan_capabilities": "解析查询目标",
    "execute_capabilities": "执行只读查询",
    "review_capabilities": "复核证据与二次查阅",
    "compose_prompt": "构建上下文",
    "generate_candidate": "生成回复",
    "parse_protocol": "解析协议",
    "repair_protocol": "修复输出协议",
    "validate_role": "校验角色一致性",
    "repair_role": "修复角色回复",
    "validate_json_update": "校验 JSON 小幅更新",
    "persist_turn": "保存本轮对话",
    "finalize_error": "整理错误",
}


@dataclass(slots=True)
class StreamEnvelopeFactory:
    run_id: str
    session_id: str
    round: int
    sequence: int = 0

    def sse(self, event: str, data: dict[str, Any] | None = None) -> str:
        self.sequence += 1
        payload = {
            "version": "1.0",
            "event": event,
            "seq": self.sequence,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "round": self.round,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data or {},
        }
        return (
            f"id: {self.sequence}\nevent: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        )


class BufferedStreamRun:
    """A turn-owned event log that survives individual HTTP subscribers."""

    def __init__(self, request_id: str, request: ChatRequest) -> None:
        self.request_id = request_id
        self.request = request
        self.events: deque[tuple[int, str]] = deque(maxlen=8192)
        self.condition = asyncio.Condition()
        self.completed = False
        self.terminal_event = ""
        self.updated_at = time.monotonic()
        self.task: asyncio.Task[None] | None = None
        self.partial_text = ""
        self.last_checkpoint_at = time.monotonic()
        self.last_checkpoint_size = 0


@dataclass(slots=True)
class ProductContainer:
    settings: AppSettings
    cancellation: CancellationRegistry
    profiles: JsonProfileRepository
    sessions: JsonSessionRepository
    knowledge: LocalKnowledgeRetriever
    memory: StructuredMemoryStore
    memory_service: StructuredMemoryService
    audit: JsonlAudit
    config: ProductConfigStore
    conversation: ConversationService
    context: ContextLedger
    compaction: ContextCompactionService
    database: ProductDatabase
    role_audit: RoleAuditService
    entities: EntityRegistry
    asr_vocabulary: ASRVocabularyStore
    capabilities: ReadOnlyCapabilityService
    emotion: DisabledEmotionCoordinator
    prompt_inspector: PromptInspectionStore


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
        )
        self.role_audit = RoleAuditService(
            ledger=dependencies.context,
            llm_provider=lambda: self.dependencies.llm,
            api_provider=self._role_audit_api,
            active_run_count=cancellation.active_count,
            enabled=lambda: self.settings.role_audit_enabled,
        )
        self._stream_runs: dict[str, BufferedStreamRun] = {}
        self._stream_runs_lock = asyncio.Lock()
        if self.dependencies.database is not None:
            # Any row still marked running belongs to a prior process. Preserve
            # its checkpoint and close it as interrupted; never replay the graph.
            self.dependencies.database.recover_interrupted_runs()
            self.dependencies.database.prune_conversation_runs(retention_hours=24)

    def _role_audit_api(self) -> ApiConfig:
        return ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.role_audit_model or self.settings.llm_model,
            temperature=0,
            max_tokens=600,
        )

    def refresh_language_model(self) -> None:
        close = getattr(self.dependencies.llm, "close", None)
        if callable(close):
            close()
        self.dependencies.llm = (
            OpenAICompatibleLanguageModel()
            if self.settings.llm_mode == "openai"
            else DeterministicLanguageModel()
        )
        if self.dependencies.context is not None:
            self.dependencies.context.configure_hard_limit(
                context_window=self.settings.llm_context_window,
                hard_ratio=self.settings.context_compaction_hard_ratio,
                reserved_tokens=self.settings.context_compaction_max_tokens,
            )

    def close(self) -> None:
        for resource in (
            self.dependencies.llm,
            self.dependencies.capabilities,
            self.dependencies.emotion,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    async def aclose(self) -> None:
        tasks = [
            run.task
            for run in self._stream_runs.values()
            if run.task is not None and not run.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.close()

    def _server_request(self, request: ChatRequest) -> ChatRequest:
        """用服务端模型地址/密钥/模型名覆盖客户端值，只保留本轮采样参数。"""

        api = ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            temperature=request.api.temperature,
            max_tokens=request.api.max_tokens,
        )
        return request.model_copy(
            update={"api": api, "server_received_at": datetime.now(UTC)}
        )

    async def invoke(self, request: ChatRequest, request_id: str | None = None) -> ChatResponse:
        request_id = request_id or uuid4().hex
        self.cancellation.start(request_id)
        response: ChatResponse | None = None
        try:
            result = await self.graph.ainvoke(
                {"request": self._server_request(request), "request_id": request_id},
                config={"recursion_limit": 20},
            )
            response = result["response"]
        finally:
            self.cancellation.finish(request_id)
            self.compaction.kick()
            self.role_audit.kick()
        return response

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

    async def resume_stream(
        self, request_id: str, *, after_sequence: int = 0
    ) -> AsyncIterator[str]:
        async with self._stream_runs_lock:
            run = self._stream_runs.get(request_id)
        if run is None:
            database = self.dependencies.database
            if database is None or database.get_conversation_run(request_id) is None:
                raise KeyError(request_id)
            for payload in database.conversation_run_events(request_id, after_sequence):
                yield payload
            return
        async for event in self._subscribe_stream(run, after_sequence):
            yield event

    async def stream_status(self, request_id: str) -> dict[str, Any] | None:
        async with self._stream_runs_lock:
            run = self._stream_runs.get(request_id)
        if run is None:
            database = self.dependencies.database
            record = database.get_conversation_run(request_id) if database is not None else None
            if record is None:
                return None
            return {
                "run_id": request_id,
                "completed": record["status"] != "running",
                "status": record["status"],
                "terminal_event": record["terminal_event"],
                "latest_seq": record["latest_seq"],
                "partial_text": record["partial_text"],
                "session_id": record["session_id"],
                "round": record["round_num"],
            }
        return {
            "run_id": request_id,
            "completed": run.completed,
            "terminal_event": run.terminal_event,
            "latest_seq": run.events[-1][0] if run.events else 0,
        }

    async def _ensure_stream_run(
        self, request: ChatRequest, request_id: str
    ) -> BufferedStreamRun:
        """创建或复用运行，并拒绝 request_id 被绑定到另一轮。"""

        async with self._stream_runs_lock:
            now = time.monotonic()
            expired = [
                key
                for key, value in self._stream_runs.items()
                if value.completed and now - value.updated_at > 600
            ]
            for key in expired:
                self._stream_runs.pop(key, None)
            existing = self._stream_runs.get(request_id)
            if existing is not None:
                if (
                    existing.request.session_id != request.session_id
                    or existing.request.round != request.round
                ):
                    raise ValueError("request id is already bound to another turn")
                return existing
            if self.dependencies.database is not None:
                durable = self.dependencies.database.create_conversation_run(
                    run_id=request_id,
                    session_id=request.session_id,
                    round_num=request.round,
                )
                if str(durable.get("status")) != "running":
                    raise ValueError("request id belongs to a completed durable run")
            run = BufferedStreamRun(request_id, request)
            self._stream_runs[request_id] = run
            run.task = asyncio.create_task(
                self._produce_stream(run), name=f"mindspace-run-{request_id[:12]}"
            )
            return run

    async def _publish_stream(
        self, run: BufferedStreamRun, sequence: int, payload: str, *, terminal: str = ""
    ) -> None:
        event_name = ""
        event_data: dict[str, Any] = {}
        for line in payload.splitlines():
            if line.startswith("event:"):
                event_name = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                try:
                    decoded = json.loads(line.partition(":")[2].strip())
                    raw_data = decoded.get("data") if isinstance(decoded, dict) else {}
                    event_data = raw_data if isinstance(raw_data, dict) else {}
                except json.JSONDecodeError:
                    event_data = {}
        async with run.condition:
            run.events.append((sequence, payload))
            run.updated_at = time.monotonic()
            if event_name == "response.delta":
                run.partial_text += str(event_data.get("delta") or "")
            elif event_name == "response.replace":
                run.partial_text = str(event_data.get("content") or "")
            if terminal:
                run.completed = True
                run.terminal_event = terminal
            run.condition.notify_all()
        database = self.dependencies.database
        if database is None:
            return
        now = time.monotonic()
        checkpoint_due = (
            now - run.last_checkpoint_at >= 0.5
            or len(run.partial_text) - run.last_checkpoint_size >= 1024
            or bool(terminal)
        )
        if checkpoint_due:
            database.checkpoint_conversation_run(
                run.request_id, run.partial_text, sequence
            )
            run.last_checkpoint_at = now
            run.last_checkpoint_size = len(run.partial_text)
        # Token deltas are represented by the coalesced partial checkpoint.
        # Milestones remain individually replayable and are capped by SQLite.
        if event_name != "response.delta":
            database.append_conversation_run_event(
                run_id=run.request_id,
                sequence=sequence,
                event=event_name or "graph.event",
                payload=payload,
                terminal=bool(terminal),
            )

    async def _subscribe_stream(
        self, run: BufferedStreamRun, after_sequence: int
    ) -> AsyncIterator[str]:
        """从 after_sequence 后重放，再跟随新事件直到终态。"""

        cursor = max(0, int(after_sequence))
        while True:
            heartbeat = False
            async with run.condition:
                available = [item for item in run.events if item[0] > cursor]
                completed = run.completed
                if not available and not completed:
                    try:
                        await asyncio.wait_for(run.condition.wait(), timeout=15.0)
                    except TimeoutError:
                        heartbeat = True
                    available = [item for item in run.events if item[0] > cursor]
                    completed = run.completed
            if heartbeat and not available:
                yield ": heartbeat\n\n"
                continue
            for sequence, payload in available:
                cursor = max(cursor, sequence)
                yield payload
            if completed and not [item for item in run.events if item[0] > cursor]:
                return

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
            async for part in self.graph.astream(
                {"request": self._server_request(request), "request_id": request_id},
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
                        if isinstance(values, dict) and isinstance(
                            values.get("response"), ChatResponse
                        ):
                            final = values["response"]
            if final is None:
                payload = events.sse("run.error", {"error": "missing final response"})
                await self._publish_stream(
                    run, events.sequence, payload, terminal="run.error"
                )
            elif final.status == "error":
                payload = events.sse(
                    "run.error", {"response": final.model_dump(mode="json")}
                )
                await self._publish_stream(
                    run, events.sequence, payload, terminal="run.error"
                )
            else:
                self.cancellation.finish(request_id)
                run_finished = True
                self.compaction.kick()
                self.role_audit.kick()
                payload = events.sse(
                    "run.completed", {"response": final.model_dump(mode="json")}
                )
                await self._publish_stream(
                    run, events.sequence, payload, terminal="run.completed"
                )
        except GenerationCancelled:
            payload = events.sse("run.cancelled", {"cancelled": True})
            await self._publish_stream(
                run, events.sequence, payload, terminal="run.cancelled"
            )
        except asyncio.CancelledError:
            # Graceful Core shutdown preserves the partial answer and exposes a
            # stable interrupted terminal instead of pretending generation failed.
            payload = events.sse(
                "run.interrupted",
                {"partial_text": run.partial_text, "reason": "core_shutdown"},
            )
            await self._publish_stream(
                run, events.sequence, payload, terminal="run.interrupted"
            )
            raise
        except Exception as exc:  # noqa: BLE001 - converted to a stable API event
            self.dependencies.audit.record(
                "stream_failed", {"request_id": request_id, "error": str(exc)}
            )
            payload = events.sse("run.error", {"error": str(exc)})
            await self._publish_stream(
                run, events.sequence, payload, terminal="run.error"
            )
        finally:
            if not run_finished:
                self.cancellation.finish(request_id)
                self.compaction.kick()
                self.role_audit.kick()
            if not run.completed:
                payload = events.sse("run.error", {"error": "stream ended unexpectedly"})
                await self._publish_stream(
                    run, events.sequence, payload, terminal="run.error"
                )

    def interrupt(self, request_id: str) -> bool:
        return self.cancellation.cancel(request_id)


def build_container(settings: AppSettings | None = None) -> ProductContainer:
    settings = settings or AppSettings.from_env()
    settings.ensure_directories()
    config = ProductConfigStore(settings.runtime_dir / "config" / "settings.json", settings)
    cancellation = CancellationRegistry()
    database = ProductDatabase(settings.runtime_dir / "data" / "context" / "context.db")
    database.begin_projection_repair()
    prompt_inspector = PromptInspectionStore(database)
    entities = EntityRegistry(database)
    profiles = JsonProfileRepository(settings.runtime_dir / "data" / "profiles", database=database)
    asr_vocabulary = ASRVocabularyStore(
        settings.runtime_dir / "data" / "asr" / "vocabulary.json",
        profiles,
    )
    sessions = JsonSessionRepository(settings.runtime_dir / "data" / "sessions", database=database)
    context = ContextLedger(
        settings.runtime_dir / "data" / "context" / "context.db", database=database
    )
    context.configure_hard_limit(
        context_window=settings.llm_context_window,
        hard_ratio=settings.context_compaction_hard_ratio,
        reserved_tokens=settings.context_compaction_max_tokens,
    )
    memory = StructuredMemoryStore(
        settings.runtime_dir / "data" / "structured-memory.json",
        database=database,
        entity_registry=entities,
    )
    memory_service = StructuredMemoryService(
        profiles, memory, database=database, entity_registry=entities
    )
    memory.migrate_entity_identities()
    knowledge = LocalKnowledgeRetriever(
        settings.runtime_dir / "data" / "knowledge.json",
        sessions=sessions,
        embedding_model_path=(settings.model_root / "shibing624" / "text2vec-base-chinese"),
        memory_store=memory,
        reranker_model_path=(
            settings.model_root / "BAAI" / "bge-reranker-base"
            if (settings.model_root / "BAAI" / "bge-reranker-base").exists()
            else None
        ),
    )
    audit = JsonlAudit(settings.runtime_dir / "logs" / "events.jsonl")
    capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: config.snapshot(redact=False),
        runtime_dir=settings.runtime_dir,
        audit=audit,
    )
    emotion = DisabledEmotionCoordinator()
    llm = (
        OpenAICompatibleLanguageModel()
        if settings.llm_mode == "openai"
        else DeterministicLanguageModel()
    )
    dependencies = Dependencies(
        retriever=knowledge,
        profiles=profiles,
        sessions=sessions,
        llm=llm,
        role_policy=RegexRolePolicy(),
        audit=audit,
        cancellation=cancellation,
        memory=memory,
        context=context,
        database=database,
        role_audit_enabled=settings.role_audit_enabled,
        entities=entities,
        capabilities=capabilities,
        emotion=emotion,
        prompt_inspector=prompt_inspector,
    )
    conversation = ConversationService(settings, dependencies, cancellation)
    return ProductContainer(
        settings=settings,
        cancellation=cancellation,
        profiles=profiles,
        sessions=sessions,
        knowledge=knowledge,
        memory=memory,
        memory_service=memory_service,
        audit=audit,
        config=config,
        conversation=conversation,
        context=context,
        compaction=conversation.compaction,
        database=database,
        role_audit=conversation.role_audit,
        entities=entities,
        asr_vocabulary=asr_vocabulary,
        capabilities=capabilities,
        emotion=emotion,
        prompt_inspector=prompt_inspector,
    )
