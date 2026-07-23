"""Versioned FastAPI product surface for Mindspace Graph."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from mindspace_graph.adapters.file_storage import _atomic_json
from mindspace_graph.audio import AudioProviderUnavailable, AudioService
from mindspace_graph.gpt_sovits import public_voice_catalog, voice_definition
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY
from mindspace_graph.models import ChatRequest
from mindspace_graph.service import ProductContainer, build_container
from mindspace_graph.settings import AppSettings
from mindspace_graph.streaming_asr import (
    ASRSessionOptions,
    FunASRStreamSession,
    apply_final_refinement,
)
from mindspace_graph.version import APP_VERSION


class InterruptRequest(BaseModel):
    request_id: str = Field(min_length=1)


class KnowledgeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500_000)
    source: str = Field(default="manual", max_length=200)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    speed: float = Field(default=1, ge=0.5, le=2)


class ClearDataRequest(BaseModel):
    scope: Literal["knowledge", "sessions", "all"]
    confirmation: str


class MemoryValueRequest(BaseModel):
    value: str | int | float | bool


class MemoryKeyRequest(BaseModel):
    memory_key: str = Field(min_length=1, max_length=500)


class EntityRequest(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=200)


class EntityAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=500)


class EntityMergeRequest(BaseModel):
    source_entity_id: str = Field(min_length=1, max_length=100)
    target_entity_id: str = Field(min_length=1, max_length=100)


class MemoryRebuildRequest(BaseModel):
    confirmation: str = ""
    dry_run: bool = True


class ASRVocabularyUpdateRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class ASRCorrectionRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=64)
    corrected_text: str = Field(min_length=1, max_length=64)


class ASRVocabularyTestRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProfileRestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=0)


PROFILE_KEYS = {
    "user": "user_profile",
    "assistant": "ai_profile",
    "ai": "ai_profile",
    "state": "runtime_state",
}


def _profile_key(name: str) -> str:
    key = PROFILE_KEYS.get(name.lower())
    if not key:
        raise HTTPException(status_code=404, detail="unknown profile document")
    return key


AVATAR_DEFAULTS: dict[str, dict[str, Any]] = {
    "user": {
        "src": "/assets/avatar-user-default.webp",
        "aspect": "2 / 3",
        "scale": 1.08,
        "x": -12,
        "y": 0,
    },
    "assistant": {
        "src": "/assets/avatar-ai-default.webp",
        "aspect": "2 / 3",
        "scale": 1.0,
        "x": 0,
        "y": 0,
    },
}
AVATAR_ASPECTS = {"2 / 3", "3 / 4", "4 / 5", "9 / 16", "1 / 1"}


def _normalize_avatar_entry(role: str, raw: Any) -> dict[str, Any]:
    default = AVATAR_DEFAULTS[role]
    value = raw if isinstance(raw, dict) else {}
    aspect = str(value.get("aspect") or default["aspect"])
    if aspect not in AVATAR_ASPECTS:
        aspect = str(default["aspect"])

    def bounded_number(key: str, minimum: float, maximum: float) -> float:
        try:
            number = float(value.get(key, default[key]))
        except (TypeError, ValueError):
            number = float(default[key])
        return max(minimum, min(maximum, number))

    return {
        "src": str(value.get("src") or default["src"]),
        "aspect": aspect,
        "scale": bounded_number("scale", 0.6, 3.0),
        "x": bounded_number("x", -80, 80),
        "y": bounded_number("y", -80, 80),
    }


def _normalize_avatar_config(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    return {role: _normalize_avatar_entry(role, value.get(role)) for role in ("user", "assistant")}


def _read_avatar_config(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if isinstance(value, dict):
                return _normalize_avatar_config(value)
        except (OSError, json.JSONDecodeError):
            pass
    return _normalize_avatar_config({})


def _voice_energy_threshold_db(
    audio_config: dict[str, Any],
    *,
    playing: bool,
    noise_floor_db: float | None,
) -> float:
    """Return the server gate; playback uses a softer candidate gate before VAD confirmation."""

    base_key = (
        "asr_barge_in_energy_threshold_db"
        if playing
        else "asr_listening_energy_threshold_db"
    )
    threshold = float(audio_config[base_key])
    if playing:
        # The AudioWorklet has already applied an adaptive floor + 8 dB gate.
        # Let weak but real speech reach VAD/ASR; only confirmed speech stops TTS.
        threshold -= 4.0
    if bool(audio_config.get("asr_adaptive_noise_enabled", True)) and noise_floor_db is not None:
        margin_key = (
            "asr_barge_in_noise_margin_db"
            if playing
            else "asr_listening_noise_margin_db"
        )
        margin = float(audio_config[margin_key])
        if playing:
            margin = max(4.0, margin - 8.0)
        threshold = max(threshold, noise_floor_db + margin)
    return min(-15.0, threshold)


def create_app(
    settings: AppSettings | None = None,
    container: ProductContainer | None = None,
) -> FastAPI:
    container = container or build_container(settings)
    settings = container.settings
    audio = AudioService(settings)
    shared_http = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await container.conversation.aclose()
        await audio.aclose()
        await shared_http.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.audio = audio

    web_root = Path(__file__).resolve().parent / "web"
    avatar_root = settings.runtime_dir / "data" / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)
    avatar_config_path = avatar_root / "config.json"
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    app.mount("/api/v1/avatar/files", StaticFiles(directory=avatar_root), name="avatars")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(web_root / "index.html")

    @app.get("/api/v1/health")
    async def health():
        return {
            "ok": True,
            "service": settings.app_name,
            "version": app.version,
            "llm_mode": settings.llm_mode,
            "runtime_dir": str(settings.runtime_dir),
        }

    @app.get("/api/v1/config")
    async def public_config():
        return {**settings.public_config(), "product": container.config.snapshot()}

    @app.get("/api/v1/settings")
    async def get_settings():
        return container.config.snapshot(redact=True)

    @app.put("/api/v1/settings")
    async def put_settings(payload: dict[str, Any]):
        try:
            result = container.config.update(payload)
            container.conversation.refresh_language_model()
            return {"success": True, "settings": result}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/audio/asr/vocabulary")
    async def get_asr_vocabulary():
        return container.asr_vocabulary.snapshot()

    @app.put("/api/v1/audio/asr/vocabulary")
    async def put_asr_vocabulary(payload: ASRVocabularyUpdateRequest):
        try:
            return container.asr_vocabulary.replace_manual(payload.entries)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/audio/asr/vocabulary/test")
    async def test_asr_vocabulary(payload: ASRVocabularyTestRequest):
        return container.asr_vocabulary.test_text(payload.text)

    @app.post("/api/v1/audio/asr/corrections")
    async def add_asr_correction(payload: ASRCorrectionRequest):
        try:
            return container.asr_vocabulary.record_correction(
                payload.raw_text, payload.corrected_text
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/audio/asr/corrections")
    async def get_asr_correction_history(limit: int = 100):
        return {"items": container.asr_vocabulary.correction_history(limit=limit)}

    @app.get("/api/v1/audio/tts/voices")
    async def list_tts_voices():
        return public_voice_catalog(settings.model_root, settings.tts_gpt_sovits_voice)

    @app.post("/api/v1/audio/tts/voice/select")
    async def select_tts_voice(payload: dict[str, Any]):
        voice_id = str(payload.get("voice_id") or "").strip()
        try:
            voice = voice_definition(voice_id)
            result = container.config.update(
                {"audio": {"tts_provider": "gpt-sovits", "tts_gpt_sovits_voice": voice_id}}
            )
            switched = await audio.select_gpt_sovits_voice(voice_id)
            return {
                "ok": True,
                "voice": voice,
                "worker": switched,
                "settings": result["audio"],
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AudioProviderUnavailable as exc:
            return {
                "ok": True,
                "pending_worker": True,
                "message": str(exc),
                "voice": voice,
                "settings": result["audio"],
            }

    @app.post("/api/v1/settings/test")
    async def test_settings():
        if settings.llm_mode != "openai":
            return {
                "ok": False,
                "mode": settings.llm_mode,
                "error": "当前未启用真实 LLM API，请保存模型 API 配置后重试",
            }
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        try:
            response = await shared_http.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": "回复 OK"}],
                    "temperature": 0,
                    "max_tokens": 2,
                    "stream": False,
                },
                timeout=8,
            )
            response.raise_for_status()
            return {
                "ok": True,
                "status_code": response.status_code,
                "detail": "真实最小生成请求成功",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @app.post("/api/v1/chat")
    async def chat(payload: ChatRequest, x_request_id: str | None = Header(default=None)):
        try:
            return await container.conversation.invoke(payload, x_request_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/v1/chat/stream")
    async def chat_stream(
        payload: ChatRequest,
        x_request_id: str | None = Header(default=None),
    ):
        return StreamingResponse(
            container.conversation.stream(payload, x_request_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/stream")
    async def resume_chat_stream(
        run_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None),
    ):
        status = await container.conversation.stream_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="run not found or replay window expired")
        header_sequence = int(last_event_id) if str(last_event_id or "").isdigit() else 0
        cursor = max(after, header_sequence)
        return StreamingResponse(
            container.conversation.resume_stream(run_id, after_sequence=cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}")
    async def get_run_status(run_id: str):
        status = await container.conversation.stream_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="run not found or replay window expired")
        return status

    @app.get("/api/v1/runs/{run_id}/prompt-inspection")
    async def prompt_inspection(run_id: str, reveal: bool = Query(default=False)):
        inspection = container.prompt_inspector.get(run_id, reveal=reveal)
        if inspection is None:
            raise HTTPException(
                status_code=404,
                detail="prompt inspection expired or is not available for this run",
            )
        return inspection

    @app.post("/api/v1/interrupt")
    async def interrupt(payload: InterruptRequest):
        graph_cancelled = container.conversation.interrupt(payload.request_id)
        audio_cancelled = audio.interrupt(payload.request_id)
        return {
            "success": graph_cancelled or audio_cancelled,
            "graph_cancelled": graph_cancelled,
            "audio_cancelled": audio_cancelled,
        }

    @app.post("/api/v1/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        return await interrupt(InterruptRequest(request_id=run_id))

    @app.get("/api/v1/sessions")
    async def list_sessions():
        return {"sessions": container.sessions.list_sessions()}

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str):
        session = container.sessions.load_session(session_id)
        session["messages"] = [
            item for item in session.get("messages", []) if not item.get("hidden")
        ]
        return session

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        with container.database.transaction(
            operation="delete_session", details={"session_id": session_id}
        ):
            if not container.sessions.delete_session(session_id):
                raise HTTPException(status_code=404, detail="session not found")
            container.memory.forget_session(session_id)
            container.context.delete_session(session_id)
        return {"success": True}

    @app.delete("/api/v1/sessions/{session_id}/rounds/{round_num}")
    async def delete_round(session_id: str, round_num: int):
        with container.database.transaction(
            operation="delete_round",
            details={"session_id": session_id, "round": round_num},
        ):
            if not container.sessions.delete_round(session_id, round_num):
                raise HTTPException(status_code=404, detail="round not found")
            container.memory.forget_session(session_id, round_num)
            container.context.invalidate(
                session_id,
                reason="round_deleted",
                details={"round": round_num},
            )
        return {"success": True}

    @app.delete("/api/v1/sessions/{session_id}/messages/{message_id}")
    async def delete_message(session_id: str, message_id: str):
        with container.database.transaction(
            operation="delete_message",
            details={"session_id": session_id, "message_id": message_id},
        ):
            event = container.sessions.delete_message(session_id, message_id)
            if event is None:
                raise HTTPException(status_code=404, detail="assistant message not found")
            container.memory.forget_message(message_id)
            container.context.invalidate(
                session_id,
                reason="assistant_message_deleted",
                details={
                    "message_id": message_id,
                    "event_id": event.event_id,
                    "deleted_content": event.deleted_content,
                },
            )
        pending = event.status == "pending"
        return {
            "success": True,
            "deletion_event_id": event.event_id if pending else None,
            "pending_json_reconciliation": pending,
        }

    @app.post("/api/v1/sessions/{session_id}/clear")
    async def clear_session(session_id: str):
        with container.database.transaction(
            operation="clear_session", details={"session_id": session_id}
        ):
            if not container.sessions.clear_session(session_id):
                raise HTTPException(status_code=404, detail="session not found")
            container.memory.forget_session(session_id)
            container.context.delete_session(session_id)
        return {"success": True}

    @app.get("/api/v1/sessions/{session_id}/context-diagnostics")
    async def context_diagnostics(session_id: str):
        return container.context.diagnostics(session_id)

    @app.get("/api/v1/memory/structured")
    async def structured_memory():
        snapshot = container.memory.snapshot()
        return {
            "stats": container.memory.stats(),
            "active": list(snapshot["active"].values()),
            "untagged": snapshot["untagged"],
        }

    @app.get("/api/v1/memory/registry")
    async def memory_registry():
        return {"fields": DEFAULT_MEMORY_REGISTRY.public()}

    @app.get("/api/v1/memory/entities")
    async def list_entities(scope: str | None = Query(default=None)):
        return {"entities": container.entities.list(scope=scope)}

    @app.post("/api/v1/memory/entities")
    async def create_entity(payload: EntityRequest):
        entity_id = container.entities.resolve(
            payload.value, scope=payload.scope, entity_type=payload.entity_type
        )
        return {"success": True, "entity_id": entity_id}

    @app.post("/api/v1/memory/entities/{entity_id}/aliases")
    async def add_entity_alias(entity_id: str, payload: EntityAliasRequest):
        try:
            return {
                "success": True,
                "alias": container.entities.add_alias(entity_id, payload.alias),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/memory/entities/merge")
    async def merge_entities(payload: EntityMergeRequest):
        try:
            with container.database.transaction(
                operation="merge_entities",
                details=payload.model_dump(mode="json"),
            ):
                container.entities.merge(payload.source_entity_id, payload.target_entity_id)
            return {"success": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/memory/items")
    async def memory_items(include_history: bool = Query(default=False)):
        items = container.memory_service.list_items(include_history=include_history)
        return {"items": items, "count": len(items)}

    @app.put("/api/v1/memory/items/{memory_key:path}")
    async def update_memory_item(memory_key: str, payload: MemoryValueRequest):
        try:
            item = container.memory_service.update(memory_key, payload.value)
            return {"success": True, "item": item}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/v1/memory/items/{memory_key:path}")
    async def delete_memory_item(memory_key: str):
        try:
            if not container.memory_service.delete(memory_key):
                raise HTTPException(status_code=404, detail="active memory not found")
            return {"success": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/memory/restore")
    async def restore_memory_item(payload: MemoryKeyRequest):
        try:
            item = container.memory_service.restore(payload.memory_key)
            return {"success": True, "item": item}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/memory/rebuild")
    async def rebuild_memory(payload: MemoryRebuildRequest):
        if not payload.dry_run and payload.confirmation != "REBUILD":
            raise HTTPException(status_code=422, detail="confirmation must be REBUILD")
        return {"success": True, **container.memory_service.rebuild(dry_run=payload.dry_run)}

    @app.get("/api/v1/chat/chunks")
    async def list_chat_chunks(session_id: str | None = Query(default=None)):
        items = container.sessions.list_chunks(session_id)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/knowledge")
    async def list_knowledge(query: str = Query(default="", max_length=200)):
        items = container.knowledge.list_knowledge(query)
        return {"items": items, "count": len(items)}

    @app.post("/api/v1/knowledge")
    async def add_knowledge(payload: KnowledgeRequest):
        chunking = container.config.snapshot(redact=False)["knowledge"]
        ids = container.knowledge.add_text(
            payload.text,
            source=payload.source,
            child_size=int(chunking["child_size"]),
            parent_size=int(chunking["parent_size"]),
            overlap=int(chunking["overlap"]),
        )
        return {"success": True, "chunk_ids": ids, "count": len(ids)}

    @app.post("/api/v1/knowledge/upload")
    async def upload_knowledge(file: Annotated[UploadFile, File()]):
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty knowledge file")
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="knowledge file exceeds 10 MiB")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="file must be UTF-8 text") from exc
        if (file.filename or "").lower().endswith(".json"):
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail="invalid JSON file") from exc
        chunking = container.config.snapshot(redact=False)["knowledge"]
        ids = container.knowledge.add_text(
            text,
            source=file.filename or "upload",
            child_size=int(chunking["child_size"]),
            parent_size=int(chunking["parent_size"]),
            overlap=int(chunking["overlap"]),
        )
        return {"success": True, "chunk_ids": ids, "count": len(ids)}

    @app.get("/api/v1/knowledge/stats")
    async def knowledge_stats():
        return container.knowledge.stats()

    @app.delete("/api/v1/knowledge/{chunk_id}")
    async def delete_knowledge(chunk_id: str):
        if not container.knowledge.delete_chunk(chunk_id):
            raise HTTPException(status_code=404, detail="knowledge chunk not found")
        return {"success": True}

    @app.get("/api/v1/profiles/{name}")
    async def get_profile(name: str):
        return container.profiles.load_document(_profile_key(name))

    @app.put("/api/v1/profiles/{name}")
    async def put_profile(name: str, payload: dict[str, Any]):
        try:
            with container.database.transaction(
                operation="user_direct_profile_edit", details={"profile": name}
            ):
                value = container.profiles.save_document(_profile_key(name), payload)
                rebuilt = container.memory_service.rebuild(dry_run=False)
                container.audit.record(
                    "user_direct_profile_edit",
                    {"profile": name, "revision": value.get("revision", 0)},
                )
            return {"success": True, "document": value, "memory_rebuild": rebuilt}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/profiles/{name}/history")
    async def profile_history(name: str, limit: int = Query(default=20, ge=1, le=100)):
        return {"items": container.profiles.list_history(_profile_key(name), limit)}

    @app.post("/api/v1/profiles/{name}/restore")
    async def restore_profile(name: str, payload: ProfileRestoreRequest):
        try:
            with container.database.transaction(
                operation="user_direct_profile_restore",
                details={"profile": name, "version_id": payload.version_id},
            ):
                value = container.profiles.restore_history(
                    _profile_key(name),
                    payload.version_id,
                    expected_revision=payload.expected_revision,
                )
                rebuilt = container.memory_service.rebuild(dry_run=False)
                container.audit.record(
                    "user_direct_profile_restore",
                    {
                        "profile": name,
                        "version_id": payload.version_id,
                        "revision": value.get("revision", 0),
                    },
                )
            return {"success": True, "document": value, "memory_rebuild": rebuilt}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/profiles/{name}/card")
    async def profile_card(name: str):
        document = container.profiles.load_document(_profile_key(name))
        return {
            "name": name,
            "identity": document.get("identity", {}),
            "personality": document.get("personality", {}),
            "relationship": document.get("relationship_state", {}),
            "revision": document.get("revision", 0),
            "updated_at": document.get("updated_at", ""),
        }

    @app.get("/api/v1/avatar/config")
    async def avatar_config():
        return _read_avatar_config(avatar_config_path)

    @app.put("/api/v1/avatar/config")
    async def put_avatar_config(payload: dict[str, Any]):
        current = _read_avatar_config(avatar_config_path)
        for role in ("user", "assistant"):
            if isinstance(payload.get(role), dict):
                current[role].update(payload[role])
        current = _normalize_avatar_config(current)
        _atomic_json(avatar_config_path, current)
        return {"success": True, "config": current}

    @app.post("/api/v1/avatar/upload/{role}")
    async def upload_avatar(
        role: Literal["user", "assistant"],
        file: Annotated[UploadFile, File()],
    ):
        content = await file.read()
        if not content or len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="avatar must be between 1 byte and 5 MiB")
        suffix = Path(file.filename or "avatar.webp").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise HTTPException(status_code=422, detail="unsupported avatar format")
        filename = f"{role}-{uuid4().hex}{suffix}"
        path = avatar_root / filename
        path.write_bytes(content)
        current = _read_avatar_config(avatar_config_path)
        current[role]["src"] = f"/api/v1/avatar/files/{filename}"
        current = _normalize_avatar_config(current)
        _atomic_json(avatar_config_path, current)
        return {"success": True, "src": current[role]["src"], "config": current}

    @app.get("/api/v1/audio/status")
    async def audio_status():
        return await audio.status()

    @app.post("/api/v1/audio/tts")
    async def synthesize(payload: TTSRequest):
        try:
            path = await audio.synthesize(
                payload.text, request_id=payload.request_id, speed=payload.speed
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=path.name,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/v1/audio/tts/stream")
    async def stream_synthesize(payload: TTSRequest):
        try:
            stream, sample_rate = await audio.stream_synthesize(
                payload.text, request_id=payload.request_id, speed=payload.speed
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(
            stream,
            media_type="application/octet-stream",
            headers={
                "X-Audio-Format": "pcm_s16le",
                "X-Audio-Sample-Rate": str(sample_rate),
                "X-Audio-Channels": "1",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/audio/tts/reference")
    async def upload_tts_reference(
        file: Annotated[UploadFile, File()],
        transcript: Annotated[str | None, Form()] = None,
    ):
        content = await file.read()
        if not content or len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="invalid reference audio")
        suffix = Path(file.filename or "reference.wav").suffix.lower()
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            raise HTTPException(status_code=422, detail="unsupported audio format")
        previous = str(settings.tts_reference_audio or "")
        path = settings.runtime_dir / "data" / "audio" / f"reference-{uuid4().hex}{suffix}"
        path.write_bytes(content)
        audio_patch: dict[str, Any] = {"tts_reference_audio": str(path)}
        if transcript is not None:
            audio_patch["tts_reference_text"] = transcript.strip()
        try:
            result = container.config.update({"audio": audio_patch})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if previous:
            candidate = Path(previous)
            audio_root = (settings.runtime_dir / "data" / "audio").resolve()
            try:
                if candidate.resolve().is_relative_to(audio_root) and candidate != path:
                    candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "success": True,
            "reference": {
                "filename": file.filename or path.name,
                "stored_name": path.name,
                "format": suffix.removeprefix("."),
                "size": len(content),
                "configured": True,
                "transcript": str(result["audio"].get("tts_reference_text") or ""),
            },
            "settings": result["audio"],
        }

    @app.delete("/api/v1/audio/tts/reference")
    async def clear_tts_reference():
        current = str(settings.tts_reference_audio or "")
        result = container.config.update(
            {"audio": {"tts_reference_audio": "", "tts_reference_text": ""}}
        )
        if current:
            candidate = Path(current)
            audio_root = (settings.runtime_dir / "data" / "audio").resolve()
            try:
                if candidate.resolve().is_relative_to(audio_root) and candidate.is_file():
                    candidate.unlink()
            except OSError:
                pass
        return {"success": True, "reference": {"configured": False}, "settings": result["audio"]}

    @app.post("/api/v1/audio/tts/reference/transcribe")
    async def transcribe_tts_reference():
        current = str(settings.tts_reference_audio or "")
        if not current:
            raise HTTPException(status_code=409, detail="请先上传参考音频")
        try:
            recognized = await audio.transcribe_reference(
                Path(current), request_id=f"tts-reference-{uuid4().hex}"
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        text = str(recognized.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="没有识别到参考音频文字")
        result = container.config.update({"audio": {"tts_reference_text": text}})
        return {
            "success": True,
            "transcript": text,
            "duration": recognized.get("duration"),
            "settings": result["audio"],
        }

    @app.post("/api/v1/audio/asr")
    async def transcribe(
        audio_file: Annotated[UploadFile, File()],
        x_request_id: str | None = Header(default=None),
    ):
        request_id = x_request_id or uuid4().hex
        data = await audio_file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty audio file")
        try:
            text = await audio.transcribe(
                data,
                audio_file.filename or "audio.webm",
                audio_file.content_type or "audio/webm",
                request_id=request_id,
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": text, "request_id": request_id}

    @app.websocket("/api/v1/audio/asr/stream")
    async def stream_asr(websocket: WebSocket):
        await websocket.accept()
        provider = settings.asr_provider
        if provider == "mock":
            await websocket.send_json({"event": "asr.ready", "data": {"provider": "mock"}})
            mock_started = False
            mock_input_locked = False
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("bytes") and not mock_started and not mock_input_locked:
                        mock_started = True
                        await websocket.send_json({"event": "asr.speech_start", "data": {}})
                        await websocket.send_json(
                            {"event": "asr.partial", "data": {"text": "这是一条测试"}}
                        )
                    if message.get("text"):
                        control = json.loads(message["text"])
                        if control.get("action") == "input_gate":
                            mock_input_locked = bool(control.get("locked", False))
                            mock_started = False
                            await websocket.send_json(
                                {
                                    "event": "asr.input_gate",
                                    "data": {"locked": mock_input_locked},
                                }
                            )
                        if control.get("action") == "stop":
                            await websocket.send_json(
                                {
                                    "event": "asr.final",
                                    "data": {"text": "这是一条测试语音", "auto_send": True},
                                }
                            )
                            mock_started = False
            except (WebSocketDisconnect, RuntimeError):
                return

        if provider != "funasr":
            await websocket.send_json(
                {"event": "asr.error", "data": {"error": f"unsupported provider: {provider}"}}
            )
            await websocket.close(code=1011)
            return

        if settings.asr_base_url.startswith(("ws://", "wss://")):
            from websockets.asyncio.client import connect

            audio_config = container.config.snapshot(redact=False)["audio"]
            stream_state: dict[str, Any] = {"playing": False, "noise_floor_db": None}

            def apply_voice_threshold(control: dict[str, Any], playing: bool) -> None:
                stream_state["playing"] = playing
                backoff_level = max(0, min(2, int(control.get("barge_backoff_level") or 0)))
                minimum_key = (
                    "asr_barge_in_min_speech_ms"
                    if playing
                    else "asr_listening_min_speech_ms"
                )
                noise_floor = stream_state.get("noise_floor_db")
                control["energy_threshold_db"] = _voice_energy_threshold_db(
                    audio_config,
                    playing=playing,
                    noise_floor_db=(
                        float(noise_floor) if isinstance(noise_floor, (int, float)) else None
                    ),
                ) + (3.0 * backoff_level if playing else 0.0)
                control["min_speech_ms"] = int(audio_config[minimum_key]) + (
                    120 * backoff_level if playing else 0
                )
                control["candidate_release_ms"] = int(audio_config["asr_candidate_release_ms"])
                control["playback_active"] = playing

            async def client_to_worker(upstream: Any) -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text"):
                        control = json.loads(message["text"])
                        if control.get("action") == "start":
                            control["silence_ms"] = int(audio_config["asr_silence_ms"])
                            control["auto_send"] = bool(audio_config["asr_auto_send"])
                            control["deferred_during_playback"] = bool(
                                audio_config.get("asr_deferred_during_playback", True)
                            )
                            control["dynamic_endpointing"] = bool(
                                audio_config.get("asr_dynamic_endpointing", True)
                            )
                            control["final_refinement_enabled"] = bool(
                                audio_config.get("asr_final_refinement_enabled", True)
                            )
                            control["final_refinement_timeout_ms"] = int(
                                audio_config.get("asr_final_refinement_timeout_ms", 1400)
                            )
                            control["final_refinement_min_audio_ms"] = int(
                                audio_config.get("asr_final_refinement_min_audio_ms", 320)
                            )
                            control["final_refinement_max_audio_ms"] = int(
                                audio_config.get("asr_final_refinement_max_audio_ms", 15000)
                            )
                            apply_voice_threshold(
                                control, bool(control.get("playback_active", False))
                            )
                            if bool(audio_config.get("asr_hotwords_enabled", True)):
                                control["vocabulary"] = container.asr_vocabulary.snapshot(
                                    include_entries=False
                                )
                        elif control.get("action") == "playback_state":
                            playing = bool(control.get("playing", False))
                            noise_floor = control.get("noise_floor_db")
                            if isinstance(noise_floor, (int, float)):
                                stream_state["noise_floor_db"] = float(noise_floor)
                            apply_voice_threshold(control, playing)
                        await upstream.send(json.dumps(control, ensure_ascii=False))

            async def worker_to_client(upstream: Any) -> None:
                async for raw in upstream:
                    event = json.loads(raw)
                    if event.get("event") in {"asr.final", "asr.deferred"}:
                        container.asr_vocabulary.record_observation(
                            event.get("data") or {}, event=str(event["event"])
                        )
                    await websocket.send_json(event)

            try:
                async with connect(settings.asr_base_url, max_size=8 * 1024 * 1024) as upstream:
                    tasks = {
                        asyncio.create_task(client_to_worker(upstream)),
                        asyncio.create_task(worker_to_client(upstream)),
                    }
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    for task in done:
                        task.result()
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json(
                    {
                        "event": "asr.error",
                        "data": {"error": f"FunASR worker unavailable: {exc}"},
                    }
                )
                await websocket.close(code=1011)
            return

        await websocket.send_json({"event": "asr.loading", "data": {"provider": "funasr"}})
        ready = await asyncio.to_thread(audio.streaming_asr.load)
        if not ready:
            await websocket.send_json(
                {
                    "event": "asr.error",
                    "data": {"error": audio.streaming_asr.error or "FunASR load failed"},
                }
            )
            await websocket.close(code=1011)
            return
        audio_config = container.config.snapshot(redact=False)["audio"]
        options = ASRSessionOptions(
            silence_ms=int(audio_config["asr_silence_ms"]),
            energy_threshold=10
            ** (float(audio_config["asr_listening_energy_threshold_db"]) / 20),
            min_speech_ms=int(audio_config["asr_listening_min_speech_ms"]),
            candidate_release_ms=int(audio_config["asr_candidate_release_ms"]),
            auto_send=bool(audio_config["asr_auto_send"]),
            deferred_during_playback=bool(
                audio_config.get("asr_deferred_during_playback", True)
            ),
            dynamic_endpointing=bool(audio_config.get("asr_dynamic_endpointing", True)),
            final_refinement_enabled=bool(
                audio_config.get("asr_final_refinement_enabled", True)
            ),
            final_refinement_timeout_ms=int(
                audio_config.get("asr_final_refinement_timeout_ms", 1400)
            ),
            final_refinement_min_audio_ms=int(
                audio_config.get("asr_final_refinement_min_audio_ms", 320)
            ),
            final_refinement_max_audio_ms=int(
                audio_config.get("asr_final_refinement_max_audio_ms", 15000)
            ),
        )
        if bool(audio_config.get("asr_hotwords_enabled", True)):
            vocabulary = container.asr_vocabulary.snapshot(include_entries=False)
            options.vocabulary_revision = str(vocabulary["revision"])
            options.decoder_hotwords = tuple(vocabulary["decoder_hotwords"])
            options.explicit_corrections = dict(vocabulary["explicit"])
            options.fuzzy_targets = tuple(vocabulary["fuzzy_targets"])
        session = FunASRStreamSession(audio.streaming_asr, options)
        await websocket.send_json(
            {
                "event": "asr.ready",
                "data": {"provider": "funasr", "sample_rate": options.sample_rate},
            }
        )
        try:
            while True:
                message = await websocket.receive()
                raw_events: list[dict[str, Any]] = []
                if message.get("bytes") is not None:
                    raw_events = await asyncio.to_thread(session.feed, message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    action = control.get("action")
                    if action == "start":
                        session.reset()
                    elif action == "playback_state":
                        playing = bool(control.get("playing", False))
                        minimum_key = (
                            "asr_barge_in_min_speech_ms"
                            if playing
                            else "asr_listening_min_speech_ms"
                        )
                        noise_floor = control.get("noise_floor_db")
                        backoff_level = max(
                            0, min(2, int(control.get("barge_backoff_level") or 0))
                        )
                        session.configure_playback(
                            playing=playing,
                            energy_threshold=10
                            ** (
                                (
                                    _voice_energy_threshold_db(
                                        audio_config,
                                        playing=playing,
                                        noise_floor_db=(
                                            float(noise_floor)
                                            if isinstance(noise_floor, (int, float))
                                            else None
                                        ),
                                    )
                                    + (3.0 * backoff_level if playing else 0.0)
                                )
                                / 20
                            ),
                            min_speech_ms=int(audio_config[minimum_key])
                            + (120 * backoff_level if playing else 0),
                            candidate_release_ms=int(
                                audio_config["asr_candidate_release_ms"]
                            ),
                            playback_text=str(control.get("playback_text") or ""),
                        )
                    elif action == "input_gate":
                        locked = bool(control.get("locked", False))
                        session.configure_input_gate(locked)
                        await websocket.send_json(
                            {
                                "event": "asr.input_gate",
                                "data": {
                                    "locked": locked,
                                    "reason": str(control.get("reason") or ""),
                                },
                            }
                        )
                    elif action == "cancel":
                        session.reset()
                        await websocket.send_json({"event": "asr.cancelled", "data": {}})
                    elif action == "stop":
                        silence = b"\x00\x00" * int(options.sample_rate * 0.5)
                        raw_events = await asyncio.to_thread(
                            session.feed, silence, force_final=True
                        )
                for event in raw_events:
                    if event.get("event") in {"asr.final", "asr.deferred"}:
                        pcm, playback_active = session.pop_finalized_audio()
                        if pcm:
                            refinement = await asyncio.to_thread(
                                audio.streaming_asr.refine_final_pcm,
                                pcm,
                                options,
                                playback_active=playback_active,
                            )
                            apply_final_refinement(event, refinement, session.corrector)
                        container.asr_vocabulary.record_observation(
                            event.get("data") or {}, event=str(event["event"])
                        )
                    await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            session.reset()

    @app.get("/api/v1/diagnostics")
    async def diagnostics():
        audio_report = await audio.status()
        return {
            "ok": True,
            "app": {"name": settings.app_name, "version": app.version},
            "paths": {
                "runtime": str(settings.runtime_dir),
                "profiles": str(container.profiles.root),
                "sessions": str(container.sessions.root),
                "knowledge": str(container.knowledge.path),
            },
            "counts": {
                "sessions": len(container.sessions.list_sessions()),
                **container.knowledge.stats(),
            },
            "audio": audio_report,
            "retrieval": container.knowledge.status(),
            "foundation": container.database.integrity_check(),
            "llm": {
                "mode": settings.llm_mode,
                "model": settings.llm_model,
                "configured": settings.llm_mode == "openai" and bool(settings.llm_api_key),
            },
        }

    @app.post("/api/v1/data/clear")
    async def clear_data(payload: ClearDataRequest):
        expected = {
            "knowledge": "CLEAR KNOWLEDGE",
            "sessions": "CLEAR SESSIONS",
            "all": "CLEAR ALL",
        }
        if payload.confirmation != expected[payload.scope]:
            raise HTTPException(status_code=422, detail="confirmation phrase does not match")
        result = {"knowledge": 0, "sessions": 0}
        if payload.scope in {"knowledge", "all"}:
            result["knowledge"] = container.knowledge.clear()
        if payload.scope in {"sessions", "all"}:
            with container.database.transaction(operation="clear_all_sessions"):
                result["sessions"] = container.sessions.clear_all()
                container.memory.reset()
                container.context.clear_all()
        return {"success": True, "removed": result}

    return app
