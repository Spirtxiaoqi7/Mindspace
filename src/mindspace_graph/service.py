"""Application service coordinating the graph, persistence, and cancellation."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mindspace_graph.adapters.file_storage import JsonProfileRepository, JsonSessionRepository
from mindspace_graph.adapters.in_memory import DeterministicLanguageModel, RegexRolePolicy
from mindspace_graph.adapters.json_audit import JsonlAudit
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever
from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.art_catalog import ArtCatalogService
from mindspace_graph.asr_vocabulary import ASRVocabularyStore
from mindspace_graph.cancellation import CancellationRegistry, GenerationCancelled
from mindspace_graph.capabilities import ReadOnlyCapabilityService
from mindspace_graph.characters import CharacterRepository
from mindspace_graph.compaction import ContextCompactionService
from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.conversation_runs import (
    BufferedStreamRun,
    ConversationRunRepository,
    StreamEnvelopeFactory,
)
from mindspace_graph.emotion_disabled import DisabledEmotionCoordinator
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.graph import build_graph
from mindspace_graph.memory_service import StructuredMemoryService
from mindspace_graph.memory_writeback import MemoryWritebackService
from mindspace_graph.event_memory import EventMemoryStore, EventMemoryWritebackService
from mindspace_graph.models import (
    ActivityPromptContext,
    ApiConfig,
    ChatRequest,
    ChatResponse,
    ScenePromptContext,
)
from mindspace_graph.ports import Dependencies
from mindspace_graph.product_config import ProductConfigStore
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.prompt_inspection import PromptInspectionStore
from mindspace_graph.role_audit import RoleAuditService
from mindspace_graph.role_runtime import build_runtime_role_state
from mindspace_graph.roleplay import effective_roleplay_max_tokens, effective_roleplay_temperature
from mindspace_graph.settings import AppSettings
from mindspace_graph.shared_chapters import SharedChapterService

RETRIEVAL_INDEX_ONLY_ROUNDS = 15
_EXPLICIT_RECALL = re.compile(r"(?:查(?:一下)?知识库|检索知识库|回忆(?:一下)?|你还记得|帮我想起|找回以前)")
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


@dataclass(slots=True)
class ProductContainer:
    settings: AppSettings
    cancellation: CancellationRegistry
    profiles: JsonProfileRepository
    sessions: JsonSessionRepository
    knowledge: LocalKnowledgeRetriever
    memory: StructuredMemoryStore
    memory_service: StructuredMemoryService
    event_memory: EventMemoryStore
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
    characters: CharacterRepository
    chapters: SharedChapterService
    art_catalog: ArtCatalogService


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
        self._retrieval_ready: set[tuple[str, str]] = set()
        self._retrieval_warmups: dict[tuple[str, str], asyncio.Task[None]] = {}

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
        close = getattr(self.dependencies.llm, "close", None)
        if callable(close):
            close()
        self.dependencies.llm = (
            OpenAICompatibleLanguageModel() if self.settings.llm_mode == "openai" else DeterministicLanguageModel()
        )
        if self.dependencies.context is not None:
            self.dependencies.context.configure_hard_limit(
                context_window=self.settings.llm_context_window,
                hard_ratio=self.settings.context_compaction_hard_ratio,
                reserved_tokens=self.settings.context_compaction_max_tokens,
            )

    def close(self) -> None:
        self.memory_writeback.close()
        self.event_memory_writeback.close()
        for task in self._retrieval_warmups.values():
            task.cancel()
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
        tasks.extend(task for task in self._retrieval_warmups.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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
        """用服务端模型地址/密钥/模型名覆盖客户端值，只保留本轮采样参数。"""

        history = list(session_snapshot) if session_snapshot is not None else self.dependencies.sessions.load_all(
            request.session_id
        )

        characters = self.dependencies.characters
        if characters is None:
            raise RuntimeError("character repository is unavailable")
        character = characters.get(request.character_id) if request.character_id else characters.default()
        if character.get("status") != "active":
            raise ValueError("selected character is archived")
        character_id = str(character["character_id"])
        session_mode = "draw" if character.get("source") == "draw" else "custom"
        profiles = self.dependencies.profiles.load_bundle(character_id)
        current_role_state = build_runtime_role_state(
            ai_profile=profiles.ai_profile,
            character_memory=profiles.character_memory,
            user_profile=profiles.user_profile,
            request_user_name=request.user_name,
            request_character_name=request.character_name,
        )
        session = self.dependencies.sessions.ensure_session(
            request.session_id,
            character_id=character_id,
            mode=session_mode,
            role_state=current_role_state,
        )
        session_role_state = session.get("role_state")
        if not isinstance(session_role_state, dict):
            session_role_state = current_role_state
        user_identity = profiles.user_profile.get("identity", {})
        ai_identity = profiles.ai_profile.get("identity", {})
        reply_context = ""
        if request.reply_to_message_id:
            for item in reversed(history):
                if str(item.get("message_id") or "") != request.reply_to_message_id or item.get("hidden"):
                    continue
                role_label = "用户" if item.get("role") == "user" else str(ai_identity.get("name") or "角色")
                content = str(item.get("content") or "").strip()
                if content:
                    reply_context = f"{role_label}：{content[:1800]}"
                break
        characters.touch(character_id)
        activity_context = None
        if request.activity_session_id:
            activities = self.dependencies.activities
            if activities is None:
                raise RuntimeError("activity service is unavailable")
            activity_context = ActivityPromptContext.model_validate(
                activities.prompt_context(request.activity_session_id, character_id=character_id)
            )
        scene_context = None
        if self.dependencies.activities is not None:
            resolved_scene = self.dependencies.activities.scene_prompt_context(
                request.session_id, character_id=character_id
            )
            if resolved_scene is not None:
                scene_context = ScenePromptContext.model_validate(resolved_scene)
        api = ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.llm_model,
            temperature=effective_roleplay_temperature(
                request,
                history,
            ),
            max_tokens=effective_roleplay_max_tokens(request, history),
        )
        retrieval_key = (request.session_id, character_id)
        explicit_recall = bool(_EXPLICIT_RECALL.search(request.message))
        default_retrieval_open = request.round > RETRIEVAL_INDEX_ONLY_ROUNDS
        index_ready = retrieval_key in self._retrieval_ready
        retrieval_ready = bool(
            not request.retrieval.rag_enabled or (index_ready and (default_retrieval_open or explicit_recall))
        )
        if retrieval_ready:
            deferred_reason = ""
        elif request.round <= RETRIEVAL_INDEX_ONLY_ROUNDS:
            deferred_reason = "index_build_only_first_15_rounds"
        else:
            deferred_reason = "index_warmup_pending"
        retrieval = request.retrieval.model_copy(
            update={
                "ready": retrieval_ready,
                "deferred_reason": deferred_reason,
            }
        )
        return request.model_copy(
            update={
                "api": api,
                "server_received_at": datetime.now(UTC),
                "voice_tts_provider": self.settings.tts_provider,
                "character_id": character_id,
                "session_mode": session_mode,
                "user_name": str(
                    session_role_state.get("user_name") or user_identity.get("preferred_name") or request.user_name
                ),
                "character_name": str(
                    session_role_state.get("character_name") or ai_identity.get("name") or request.character_name
                ),
                "system_prompt": str(character.get("system_prompt") or ""),
                "activity_context": activity_context,
                "scene_context": scene_context,
                "reply_context": reply_context,
                "retrieval": retrieval,
            }
        )

    def _kick_retrieval_warmup(self, request: ChatRequest) -> None:
        """Warm retrieval only after the foreground turn has been committed."""

        if not request.retrieval.rag_enabled:
            return
        key = (request.session_id, request.character_id)
        if key in self._retrieval_ready:
            return
        existing = self._retrieval_warmups.get(key)
        if existing is not None and not existing.done():
            return
        prewarm = getattr(self.dependencies.retriever, "prewarm", None)
        if not callable(prewarm):
            self._retrieval_ready.add(key)
            return
        messages = self.dependencies.sessions.load_all(request.session_id)

        async def worker() -> None:
            started = time.perf_counter()
            self.dependencies.audit.record(
                "retrieval_warmup_started",
                {
                    "session_id": request.session_id,
                    "character_id": request.character_id,
                },
            )
            try:
                details = await asyncio.to_thread(
                    prewarm,
                    session_id=request.session_id,
                    character_id=request.character_id,
                    messages=messages,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - foreground chat already succeeded
                self.dependencies.audit.record(
                    "retrieval_warmup_failed",
                    {
                        "session_id": request.session_id,
                        "character_id": request.character_id,
                        "error": str(exc)[:500],
                    },
                )
            else:
                self._retrieval_ready.add(key)
                self.dependencies.audit.record(
                    "retrieval_warmup_completed",
                    {
                        "session_id": request.session_id,
                        "character_id": request.character_id,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                        "details": details,
                    },
                )
            finally:
                self._retrieval_warmups.pop(key, None)

        self._retrieval_warmups[key] = asyncio.create_task(
            worker(),
            name=f"retrieval-warmup-{request.session_id[:12]}",
        )

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
                self._kick_retrieval_warmup(server_request)
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


def build_container(settings: AppSettings | None = None) -> ProductContainer:
    settings = settings or AppSettings.from_env()
    settings.ensure_directories()
    config = ProductConfigStore(settings.runtime_dir / "config" / "settings.json", settings)
    cancellation = CancellationRegistry()
    database = ProductDatabase(settings.runtime_dir / "data" / "context" / "context.db")
    event_memory = EventMemoryStore(database)
    database.begin_projection_repair()
    prompt_inspector = PromptInspectionStore(database)
    entities = EntityRegistry(database)
    profiles = JsonProfileRepository(settings.runtime_dir / "data" / "profiles", database=database)
    asr_vocabulary = ASRVocabularyStore(
        settings.runtime_dir / "data" / "asr" / "vocabulary.json",
        profiles,
    )
    sessions = JsonSessionRepository(settings.runtime_dir / "data" / "sessions", database=database)
    characters = CharacterRepository(
        settings.runtime_dir / "data" / "characters",
        database=database,
        profiles=profiles,
        sessions=sessions,
        avatar_config_path=settings.runtime_dir / "data" / "avatars" / "config.json",
    )
    profiles.bind_characters(characters)
    context = ContextLedger(settings.runtime_dir / "data" / "context" / "context.db", database=database)
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
    memory.bind_legacy_character(str(characters.default()["character_id"]))
    memory_service = StructuredMemoryService(profiles, memory, database=database, entity_registry=entities)
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
    llm = OpenAICompatibleLanguageModel() if settings.llm_mode == "openai" else DeterministicLanguageModel()
    chapters = SharedChapterService(
        database,
        characters=characters,
        sessions=sessions,
        audit=audit,
        llm_provider=lambda: dependencies.llm,
        api_provider=lambda: ApiConfig(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=0.65,
            max_tokens=1_000,
        ),
    )
    art_catalog = ArtCatalogService(
        Path(__file__).resolve().parent / "web" / "archive" / "manifest.json",
        settings.runtime_dir / "data" / "assets" / "packs",
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
        event_memory=event_memory,
        context=context,
        database=database,
        role_audit_enabled=settings.role_audit_enabled,
        entities=entities,
        capabilities=capabilities,
        emotion=emotion,
        prompt_inspector=prompt_inspector,
        characters=characters,
        activities=chapters,
        tts_provider=lambda: settings.tts_provider,
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
        event_memory=event_memory,
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
        characters=characters,
        chapters=chapters,
        art_catalog=art_catalog,
    )
