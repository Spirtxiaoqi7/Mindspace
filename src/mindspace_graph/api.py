"""Versioned FastAPI product surface for Mindspace Graph."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import zipfile
from urllib.parse import quote
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
from mindspace_graph.characters import (
    CORE_TRAITS,
    FLAWS,
    RELATIONSHIPS,
    CharacterDraftInput,
    generate_draft_once,
)
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
    voice_cue: str = Field(default="neutral", max_length=32)


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


class CharacterRestoreRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class SessionCreateRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    character_id: str = Field(min_length=1, max_length=64)


def _character_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "character_id",
            "schema_version",
            "revision",
            "source",
            "status",
            "display_name",
            "gender",
            "user_alias",
            "relationship_label",
            "avatar",
            "created_at",
            "updated_at",
            "last_used_at",
        )
    }


def _avatar_suffix(filename: str, data: bytes) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("unsupported avatar format")
    valid = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith((b"GIF87a", b"GIF89a"))
    )
    if not valid:
        raise ValueError("avatar content does not match a supported image")
    return ".jpg" if suffix == ".jpeg" else suffix


def _safe_archive_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-") or "character"


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
    """Return a stable server gate before FunASR VAD confirms real speech.

    Older clients reported a short startup noise sample and raised the gate from
    that value.  The sample was both timing-sensitive and easy to contaminate
    with the user's first words, so it could suppress quiet Chinese speech.
    Keep the argument for wire compatibility, but deliberately do not use it.
    """

    base_key = (
        "asr_barge_in_energy_threshold_db"
        if playing
        else "asr_listening_energy_threshold_db"
    )
    threshold = float(audio_config[base_key])
    if playing:
        # TTS playback still needs echo rejection, but the energy gate is only a
        # candidate filter. FunASR VAD and semantic arbitration decide whether
        # the assistant is actually interrupted.
        threshold -= 4.0
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
    character_root = settings.runtime_dir / "data" / "characters"
    character_root.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    app.mount("/api/v1/avatar/files", StaticFiles(directory=avatar_root), name="avatars")
    app.mount(
        "/api/v1/character/files",
        StaticFiles(directory=character_root),
        name="character-files",
    )

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

    @app.get("/api/v1/audio/tts/qwen3/voices")
    async def list_qwen3_tts_voices():
        try:
            return await audio.qwen3_vllm_voices()
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    @app.get("/api/v1/characters/options")
    async def character_options():
        return {
            "core_traits": CORE_TRAITS,
            "flaws": FLAWS,
            "relationships": RELATIONSHIPS,
            "gender": ["女", "男"],
        }

    @app.get("/api/v1/characters")
    async def list_characters(include_archived: bool = Query(default=False)):
        sessions = container.sessions.list_sessions()
        latest_session: dict[str, str] = {}
        session_count: dict[str, int] = {}
        for session in sessions:
            character_id = str(session.get("character_id") or "")
            if not character_id:
                continue
            session_count[character_id] = session_count.get(character_id, 0) + 1
            latest_session.setdefault(character_id, str(session.get("session_id") or ""))
        items = []
        for record in container.characters.list(include_archived=include_archived):
            summary = _character_summary(record)
            character_id = str(record["character_id"])
            summary["session_count"] = session_count.get(character_id, 0)
            summary["latest_session_id"] = latest_session.get(character_id, "")
            items.append(summary)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/characters/{character_id}")
    async def get_character(character_id: str):
        try:
            return container.characters.get(character_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters")
    async def create_character(payload: dict[str, Any]):
        try:
            profile = payload.get("ai_profile")
            if not isinstance(profile, dict):
                raise ValueError("ai_profile is required")
            record = container.characters.create(
                ai_profile=profile,
                source=str(payload.get("source") or "custom"),
                runtime_state=payload.get("runtime_state"),
                avatar=payload.get("avatar"),
                user_alias=str(payload.get("user_alias") or ""),
                relationship_label=str(payload.get("relationship_label") or ""),
                system_prompt=str(payload.get("system_prompt") or ""),
            )
            container.memory_service.rebuild(dry_run=False)
            return {"success": True, "character": record}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/characters/{character_id}")
    async def update_character(character_id: str, payload: dict[str, Any]):
        try:
            record = container.characters.update(character_id, payload)
            container.memory_service.rebuild(dry_run=False)
            container.audit.record(
                "user_direct_character_edit",
                {"character_id": character_id, "revision": record["revision"]},
            )
            return {"success": True, "character": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/clone")
    async def clone_character(character_id: str):
        try:
            return {
                "success": True,
                "character": container.characters.clone(character_id),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/archive")
    async def archive_character(character_id: str):
        try:
            current = container.characters.get(character_id)
            active_count = len(container.characters.list())
            next_status = "active" if current.get("status") == "archived" else "archived"
            if next_status == "archived" and active_count <= 1:
                raise ValueError("至少保留一个可用角色")
            return {
                "success": True,
                "character": container.characters.update(
                    character_id,
                    {"revision": current["revision"], "status": next_status},
                ),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/characters/{character_id}/history")
    async def character_history(
        character_id: str, limit: int = Query(default=20, ge=1, le=100)
    ):
        try:
            container.characters.get(character_id)
            return {"items": container.characters.history(character_id, limit)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/restore")
    async def restore_character(character_id: str, payload: CharacterRestoreRequest):
        try:
            record = container.characters.restore(
                character_id, payload.version_id, payload.expected_revision
            )
            container.memory_service.rebuild(dry_run=False)
            return {"success": True, "character": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/characters/{character_id}/export")
    async def export_character(character_id: str):
        try:
            record = container.characters.get(character_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        profile_bytes = json.dumps(
            record["ai_profile"], ensure_ascii=False, indent=2
        ).encode("utf-8")
        files: dict[str, bytes] = {"ai-profile.json": profile_bytes}
        avatar_src = str((record.get("avatar") or {}).get("src") or "")
        prefix = "/api/v1/character/files/"
        if avatar_src.startswith(prefix):
            relative = Path(avatar_src.removeprefix(prefix))
            candidate = (character_root / relative).resolve()
            root_resolved = character_root.resolve()
            if candidate.is_file() and root_resolved in candidate.parents:
                files[f"avatar{candidate.suffix.lower()}"] = candidate.read_bytes()
        manifest = {
            "format": "mindspace-card",
            "schema_version": "1.0.0",
            "display_name": record["display_name"],
            "gender": record["gender"],
            "relationship_label": record.get("relationship_label", ""),
            "user_alias": record.get("user_alias", ""),
            "files": {
                name: {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in files.items()
            },
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for name, data in files.items():
                archive.writestr(name, data)
        buffer.seek(0)
        filename = f"{_safe_archive_name(str(record['display_name']))}.mindspace-card"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.mindspace.character+zip",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=\"mindspace-card.mindspace-card\"; "
                    f"filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    @app.post("/api/v1/characters/import")
    async def import_character(file: Annotated[UploadFile, File()]):
        data = await file.read()
        if not data or len(data) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail="character package must be between 1 byte and 10 MiB"
            )
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 4:
                    raise ValueError("character package contains too many files")
                names = [item.filename for item in infos]
                for info in infos:
                    path = Path(info.filename)
                    if (
                        info.is_dir()
                        or path.is_absolute()
                        or ".." in path.parts
                        or len(info.filename) > 100
                        or info.file_size > 5 * 1024 * 1024
                    ):
                        raise ValueError("unsafe character package entry")
                if "manifest.json" not in names or "ai-profile.json" not in names:
                    raise ValueError("character package is missing required files")
                allowed = {
                    "manifest.json",
                    "ai-profile.json",
                    "avatar.png",
                    "avatar.jpg",
                    "avatar.webp",
                    "avatar.gif",
                }
                if any(name not in allowed for name in names):
                    raise ValueError("character package contains unsupported files")
                manifest = json.loads(archive.read("manifest.json"))
                if (
                    manifest.get("format") != "mindspace-card"
                    or manifest.get("schema_version") != "1.0.0"
                ):
                    raise ValueError("unsupported character package version")
                payload_files = manifest.get("files")
                if not isinstance(payload_files, dict):
                    raise ValueError("character package file manifest is invalid")
                for name, expected in payload_files.items():
                    if name not in names or not isinstance(expected, dict):
                        raise ValueError("character package file manifest is incomplete")
                    content = archive.read(name)
                    if len(content) != int(expected.get("bytes", -1)):
                        raise ValueError("character package file size mismatch")
                    if hashlib.sha256(content).hexdigest() != expected.get("sha256"):
                        raise ValueError("character package checksum mismatch")
                profile = json.loads(archive.read("ai-profile.json"))
                avatar_name = next(
                    (name for name in names if name.startswith("avatar.")), ""
                )
                avatar_data = archive.read(avatar_name) if avatar_name else b""
            record = container.characters.create(
                ai_profile=profile,
                source="imported",
                user_alias=str(manifest.get("user_alias") or ""),
                relationship_label=str(manifest.get("relationship_label") or ""),
            )
            if avatar_name and avatar_data:
                suffix = _avatar_suffix(avatar_name, avatar_data)
                target_dir = character_root / str(record["character_id"])
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f"avatar{suffix}"
                target.write_bytes(avatar_data)
                avatar = {
                    "src": f"/api/v1/character/files/{record['character_id']}/{target.name}",
                    "aspect": "2 / 3",
                    "scale": 1.0,
                    "x": 0,
                    "y": 0,
                }
                record = container.characters.update(
                    str(record["character_id"]),
                    {"revision": record["revision"], "avatar": avatar},
                )
            container.memory_service.rebuild(dry_run=False)
            return {"success": True, "character": record}
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/character-drafts")
    async def create_character_draft(payload: CharacterDraftInput):
        try:
            return container.characters.create_draft(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/character-drafts/{draft_id}")
    async def get_character_draft(draft_id: str):
        try:
            return container.characters.get_draft(draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/v1/character-drafts/{draft_id}")
    async def update_character_draft(draft_id: str, payload: dict[str, Any]):
        try:
            updates: dict[str, Any] = {}
            if "input" in payload:
                selected = CharacterDraftInput.model_validate(payload["input"])
                from mindspace_graph.characters import (
                    local_profile_from_draft,
                    validate_character_combination,
                )

                conflicts = validate_character_combination(selected)
                if conflicts:
                    raise ValueError("；".join(conflicts))
                updates["input"] = selected.model_dump(mode="json")
                updates["profile"] = local_profile_from_draft(selected)
                updates["generation_mode"] = "local_template"
                updates["model_call_count"] = 0
                updates["warnings"] = []
            if "profile" in payload:
                updates["profile"] = payload["profile"]
            if "avatar" in payload:
                avatar = payload["avatar"]
                if not isinstance(avatar, dict):
                    raise ValueError("avatar must be an object")
                src = str(avatar.get("src") or "")
                if not src.startswith("/assets/characters/"):
                    raise ValueError("only bundled placeholder avatars can be selected directly")
                updates["avatar"] = {
                    "src": src,
                    "aspect": str(avatar.get("aspect") or "2 / 3"),
                    "scale": float(avatar.get("scale", 1.0)),
                    "x": float(avatar.get("x", 0)),
                    "y": float(avatar.get("y", 0)),
                }
            return container.characters.save_draft(draft_id, updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/character-drafts/{draft_id}/generate")
    async def generate_character_draft(draft_id: str):
        try:
            return await generate_draft_once(
                container.characters,
                draft_id,
                llm=container.conversation.dependencies.llm,
                settings=settings,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/character-drafts/{draft_id}/avatar")
    async def upload_character_draft_avatar(
        draft_id: str, file: Annotated[UploadFile, File()]
    ):
        try:
            container.characters.get_draft(draft_id)
            data = await file.read()
            if not data or len(data) > 5 * 1024 * 1024:
                raise ValueError("avatar must be between 1 byte and 5 MiB")
            suffix = _avatar_suffix(file.filename or "", data)
            relative = Path("drafts") / f"{draft_id}{suffix}"
            target = character_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            avatar = {
                "src": f"/api/v1/character/files/{relative.as_posix()}",
                "aspect": "2 / 3",
                "scale": 1.0,
                "x": 0,
                "y": 0,
            }
            draft = container.characters.save_draft(draft_id, {"avatar": avatar})
            return {"success": True, "avatar": avatar, "draft": draft}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/character-drafts/{draft_id}/commit")
    async def commit_character_draft(draft_id: str, payload: dict[str, Any] | None = None):
        try:
            draft = container.characters.get_draft(draft_id)
            selected = CharacterDraftInput.model_validate(draft["input"])
            with container.database.transaction(
                operation="commit_character_draft", details={"draft_id": draft_id}
            ):
                user_profile = container.profiles.load_document("user_profile")
                if (
                    str(user_profile.get("identity", {}).get("preferred_name") or "")
                    != selected.user_name
                ):
                    user_profile["identity"]["preferred_name"] = selected.user_name
                    container.profiles.save_document("user_profile", user_profile)
                record = container.characters.commit_draft(
                    draft_id,
                    (payload or {}).get("profile") if isinstance(payload, dict) else None,
                )
                avatar = record.get("avatar") or {}
                src = str(avatar.get("src") or "")
                prefix = "/api/v1/character/files/drafts/"
                if src.startswith(prefix):
                    draft_file = character_root / "drafts" / src.removeprefix(prefix)
                    if draft_file.is_file():
                        target_dir = character_root / str(record["character_id"])
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target = target_dir / f"avatar{draft_file.suffix.lower()}"
                        shutil.copy2(draft_file, target)
                        avatar["src"] = (
                            f"/api/v1/character/files/{record['character_id']}/{target.name}"
                        )
                        record = container.characters.update(
                            str(record["character_id"]),
                            {"revision": record["revision"], "avatar": avatar},
                        )
                container.memory_service.rebuild(dry_run=False)
            return {"success": True, "character": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/sessions")
    async def create_session(payload: SessionCreateRequest):
        try:
            character = container.characters.get(payload.character_id)
            if character.get("status") != "active":
                raise ValueError("selected character is archived")
            mode = "draw" if character.get("source") == "draw" else "custom"
            session = container.sessions.ensure_session(
                payload.session_id,
                character_id=payload.character_id,
                mode=mode,
            )
            container.characters.touch(payload.character_id)
            return session
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
        character_map = {
            str(item["character_id"]): _character_summary(item)
            for item in container.characters.list(include_archived=True)
        }
        sessions = container.sessions.list_sessions()
        for session in sessions:
            character = character_map.get(str(session.get("character_id") or ""))
            if character:
                session["character_name"] = character["display_name"]
                session["character_avatar"] = character["avatar"]
                session["character_source"] = character["source"]
        return {"sessions": sessions}

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str):
        session = container.sessions.load_session(session_id)
        session["messages"] = [
            item for item in session.get("messages", []) if not item.get("hidden")
        ]
        character_id = str(session.get("character_id") or "")
        if character_id:
            try:
                session["character"] = _character_summary(
                    container.characters.get(character_id)
                )
            except KeyError:
                session["character_missing"] = True
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
    async def memory_items(
        include_history: bool = Query(default=False),
        character_id: str = Query(default="", max_length=64),
    ):
        items = container.memory_service.list_items(
            include_history=include_history, character_id=character_id
        )
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
    async def rebuild_memory(
        payload: MemoryRebuildRequest,
        character_id: str = Query(default="", max_length=64),
    ):
        if not payload.dry_run and payload.confirmation != "REBUILD":
            raise HTTPException(status_code=422, detail="confirmation must be REBUILD")
        return {
            "success": True,
            **container.memory_service.rebuild(
                dry_run=payload.dry_run, character_id=character_id
            ),
        }

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
    async def get_profile(
        name: str, character_id: str = Query(default="", max_length=64)
    ):
        return container.profiles.load_document(_profile_key(name), character_id)

    @app.put("/api/v1/profiles/{name}")
    async def put_profile(
        name: str,
        payload: dict[str, Any],
        character_id: str = Query(default="", max_length=64),
    ):
        try:
            with container.database.transaction(
                operation="user_direct_profile_edit", details={"profile": name}
            ):
                value = container.profiles.save_document(
                    _profile_key(name), payload, character_id
                )
                rebuilt = container.memory_service.rebuild(
                    dry_run=False, character_id=character_id
                )
                container.audit.record(
                    "user_direct_profile_edit",
                    {
                        "profile": name,
                        "character_id": character_id,
                        "revision": value.get("revision", 0),
                    },
                )
            return {"success": True, "document": value, "memory_rebuild": rebuilt}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/profiles/{name}/history")
    async def profile_history(
        name: str,
        limit: int = Query(default=20, ge=1, le=100),
        character_id: str = Query(default="", max_length=64),
    ):
        if character_id and _profile_key(name) in {"ai_profile", "runtime_state"}:
            return {"items": container.characters.history(character_id, limit)}
        return {"items": container.profiles.list_history(_profile_key(name), limit)}

    @app.post("/api/v1/profiles/{name}/restore")
    async def restore_profile(
        name: str,
        payload: ProfileRestoreRequest,
        character_id: str = Query(default="", max_length=64),
    ):
        try:
            with container.database.transaction(
                operation="user_direct_profile_restore",
                details={"profile": name, "version_id": payload.version_id},
            ):
                if character_id and _profile_key(name) in {
                    "ai_profile",
                    "runtime_state",
                }:
                    current = container.characters.get(character_id)
                    restored = container.characters.restore(
                        character_id,
                        payload.version_id,
                        int(current["revision"]),
                    )
                    value = restored[_profile_key(name)]
                else:
                    value = container.profiles.restore_history(
                        _profile_key(name),
                        payload.version_id,
                        expected_revision=payload.expected_revision,
                    )
                rebuilt = container.memory_service.rebuild(
                    dry_run=False, character_id=character_id
                )
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
    async def profile_card(
        name: str, character_id: str = Query(default="", max_length=64)
    ):
        document = container.profiles.load_document(_profile_key(name), character_id)
        return {
            "name": name,
            "identity": document.get("identity", {}),
            "personality": document.get("personality", {}),
            "relationship": document.get("relationship_state", {}),
            "roleplay": document.get("roleplay", {}),
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

    @app.get("/api/v1/audio/diagnostics")
    async def audio_diagnostics():
        """Return operational voice state without audio, transcript, or credentials."""

        status = await audio.status()
        asr_detail = status.get("asr_detail")
        native = asr_detail.get("native_capture", {}) if isinstance(asr_detail, dict) else {}
        return {
            "asr_ready": bool(status.get("asr_ready")),
            "tts_ready": bool(status.get("tts_ready")),
            "capture": {
                key: native.get(key)
                for key in (
                    "capture_state",
                    "capture_endpoint",
                    "device_name",
                    "source_sample_rate",
                    "sample_rate",
                    "first_pcm_ms",
                    "last_pcm_age_ms",
                    "subscribers",
                    "restart_count",
                    "last_restart_reason",
                    "error_code",
                    "error",
                )
            },
            "tts_queue": status.get("tts_queue", {}),
            "tts": {
                "provider": status.get("tts_provider"),
                "detail": status.get("tts_detail", {}),
                "metrics": status.get("tts_metrics", {}),
            },
        }

    @app.post("/api/v1/audio/tts")
    async def synthesize(payload: TTSRequest):
        try:
            path = await audio.synthesize(
                payload.text,
                request_id=payload.request_id,
                speed=payload.speed,
                voice_cue=payload.voice_cue,
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
                payload.text,
                request_id=payload.request_id,
                speed=payload.speed,
                voice_cue=payload.voice_cue,
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
                "X-TTS-Provider": settings.tts_provider,
                "X-TTS-Text-Mode": (
                    "full-response"
                    if settings.tts_provider == "qwen3-vllm"
                    else "streamed-segments"
                ),
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
                    if message.get("type") == "websocket.disconnect":
                        return
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
                    # Always retrieve every task result. Otherwise a client
                    # disconnect racing the upstream close is logged as
                    # "Task exception was never retrieved" and leaves the
                    # bridge looking like a service crash.
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, asyncio.CancelledError):
                            continue
                        if isinstance(result, (WebSocketDisconnect, RuntimeError)):
                            return
                        if isinstance(result, BaseException):
                            raise result
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception as exc:  # noqa: BLE001
                try:
                    await websocket.send_json(
                        {
                            "event": "asr.error",
                            "data": {"error": f"FunASR worker unavailable: {exc}"},
                        }
                    )
                    await websocket.close(code=1011)
                except (WebSocketDisconnect, RuntimeError):
                    pass
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
                if message.get("type") == "websocket.disconnect":
                    break
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
