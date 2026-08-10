"""Character, V2 card, profile, avatar, art, journal, and moment routes."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from mindspace_graph.adapters.file_storage import _atomic_json
from mindspace_graph.art_catalog import ArtPackPaused
from mindspace_graph.shared_chapters import (
    JournalCreate,
    JournalUpdate,
    MomentCreate,
    MomentUpdate,
)

from .context import (
    ApiContext,
    CharacterRestoreRequest,
    JournalGenerateRequest,
    ProfileRestoreRequest,
    _avatar_suffix,
    _character_summary,
    _normalize_avatar_config,
    _profile_key,
    _read_avatar_config,
    _safe_archive_name,
)


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container
    avatar_root = context.avatar_root
    avatar_config_path = context.avatar_config_path
    character_root = context.character_root

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
            summary["chapters"] = (
                container.chapters.summary(character_id)
                if record.get("status") == "active"
                else {
                    "journal_count": 0,
                    "moment_count": 0,
                    "candidate_moment_count": 0,
                    "activity_count": 0,
                    "heart_state": "empty",
                }
            )
            items.append(summary)
        return {"items": items, "count": len(items)}

    @app.get("/api/v1/art/catalog")
    async def art_catalog():
        return container.art_catalog.catalog()

    @app.get("/api/v1/art/packs")
    async def art_packs():
        return {"items": container.art_catalog.packs()}

    @app.post("/api/v1/art/packs/{pack_id}/install")
    async def install_art_pack(pack_id: str):
        try:
            result = await asyncio.to_thread(container.art_catalog.install, pack_id)
            return {"success": True, "pack": result}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ArtPackPaused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/v1/art/packs/{pack_id}/pause")
    async def pause_art_pack(pack_id: str):
        try:
            return {"success": True, "pack": container.art_catalog.pause(pack_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/art/packs/{pack_id}/resume")
    async def resume_art_pack(pack_id: str):
        try:
            result = await asyncio.to_thread(container.art_catalog.install, pack_id)
            return {"success": True, "pack": result}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArtPackPaused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/characters/{character_id}/chapters/summary")
    async def shared_chapter_summary(character_id: str):
        try:
            return container.chapters.summary(character_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/characters/{character_id}/journal")
    async def list_journal(character_id: str, include_archived: bool = Query(default=False)):
        try:
            items = container.chapters.list_journals(character_id, include_archived=include_archived)
            return {"items": items, "count": len(items)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/journal")
    async def create_journal(character_id: str, payload: JournalCreate):
        try:
            return container.chapters.create_journal(character_id, payload)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/journal/generate")
    async def generate_journal(character_id: str, payload: JournalGenerateRequest):
        try:
            return await asyncio.to_thread(
                container.chapters.generate_journal,
                character_id,
                session_id=payload.session_id,
                activity_session_id=payload.activity_session_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/characters/{character_id}/journal/{entry_id}")
    async def update_journal(character_id: str, entry_id: str, payload: JournalUpdate):
        try:
            return container.chapters.update_journal(character_id, entry_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/v1/characters/{character_id}/journal/{entry_id}")
    async def delete_journal(character_id: str, entry_id: str):
        if not container.chapters.delete_journal(character_id, entry_id):
            raise HTTPException(status_code=404, detail="journal entry not found")
        return {"success": True}

    @app.get("/api/v1/characters/{character_id}/moments")
    async def list_moments(character_id: str, include_archived: bool = Query(default=False)):
        try:
            items = container.chapters.list_moments(character_id, include_archived=include_archived)
            return {"items": items, "count": len(items)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/moments")
    async def create_moment(character_id: str, payload: MomentCreate):
        try:
            return container.chapters.create_moment(character_id, payload)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/characters/{character_id}/moments/{moment_id}")
    async def update_moment(character_id: str, moment_id: str, payload: MomentUpdate):
        try:
            return container.chapters.update_moment(character_id, moment_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/characters/{character_id}")
    async def get_character(character_id: str):
        try:
            return container.characters.get(character_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters")
    async def create_character(payload: dict[str, Any]):
        try:
            card = payload.get("card")
            if not isinstance(card, dict):
                raise ValueError("card must be a Character Card V2 object")
            record = container.characters.create(
                card=card,
                source=str(payload.get("source") or "custom"),
                avatar=payload.get("avatar"),
                user_alias=str(payload.get("user_alias") or ""),
                relationship_label=str(payload.get("relationship_label") or ""),
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
    async def character_history(character_id: str, limit: int = Query(default=20, ge=1, le=100)):
        try:
            container.characters.get(character_id)
            return {"items": container.characters.history(character_id, limit)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/characters/{character_id}/restore")
    async def restore_character(character_id: str, payload: CharacterRestoreRequest):
        try:
            record = container.characters.restore(character_id, payload.version_id, payload.expected_revision)
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

        if isinstance(record.get("card"), dict):
            payload = json.dumps(record["card"], ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"{_safe_archive_name(str(record['display_name']))}.json"
            return Response(
                content=payload,
                media_type="application/json; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
            )
        profile_bytes = json.dumps(record["ai_profile"], ensure_ascii=False, indent=2).encode("utf-8")
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
                    f"attachment; filename=\"mindspace-card.mindspace-card\"; filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    @app.get("/api/v1/characters/{character_id}/card")
    async def get_character_card(character_id: str):
        try:
            card = container.characters.get(character_id).get("card")
            if not isinstance(card, dict):
                raise ValueError("character is awaiting V2 migration")
            return card
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/characters/import")
    async def import_character(file: Annotated[UploadFile, File()]):
        data = await file.read()
        if not data or len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="character package must be between 1 byte and 10 MiB")
        try:
            decoded = json.loads(data.decode("utf-8"))
            if isinstance(decoded, dict) and decoded.get("spec") == "chara_card_v2":
                record = container.characters.create(card=decoded, source="imported")
                container.memory_service.rebuild(dry_run=False)
                return {"success": True, "character": record}
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
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
                if manifest.get("format") != "mindspace-card" or manifest.get("schema_version") != "1.0.0":
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
                avatar_name = next((name for name in names if name.startswith("avatar.")), "")
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

    @app.get("/api/v1/profiles/{name}")
    async def get_profile(name: str, character_id: str = Query(default="", max_length=64)):
        return container.profiles.load_document(_profile_key(name), character_id)

    @app.put("/api/v1/profiles/{name}")
    async def put_profile(
        name: str,
        payload: dict[str, Any],
        character_id: str = Query(default="", max_length=64),
    ):
        try:
            profile_key = _profile_key(name)
            with container.database.transaction(operation="user_direct_profile_edit", details={"profile": name}):
                value = container.profiles.save_document(profile_key, payload, character_id)
                rebuilt = (
                    {"skipped": True, "reason": "compact_user_profile_has_no_memory_index"}
                    if profile_key == "user_profile"
                    else container.memory_service.rebuild(dry_run=False, character_id=character_id)
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
                rebuilt = container.memory_service.rebuild(dry_run=False, character_id=character_id)
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
    async def profile_card(name: str, character_id: str = Query(default="", max_length=64)):
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


__all__ = ["register_routes"]
