"""FastAPI app factory and route composition for Mindspace Graph."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mindspace_graph.api_routes import (
    register_audio_scene_routes,
    register_character_routes,
    register_chat_routes,
    register_destiny_routes,
    register_legacy_routes,
    register_memory_routes,
    register_system_routes,
)
from mindspace_graph.api_routes.context import (
    ApiContext,
    _character_summary,
    _voice_energy_threshold_db,
)
from mindspace_graph.audio import AudioService
from mindspace_graph.bootstrap import ProductContainer, build_container
from mindspace_graph.destiny import DestinyService
from mindspace_graph.settings import AppSettings
from mindspace_graph.static_paths import STATIC_APP_ROOT
from mindspace_graph.version import APP_VERSION


def create_app(
    settings: AppSettings | None = None,
    container: ProductContainer | None = None,
) -> FastAPI:
    container = container or build_container(settings)
    settings = container.settings
    audio = AudioService(settings)
    destiny = DestinyService(
        container.database,
        characters=container.characters,
        profiles=container.profiles,
        llm=container.conversation.dependencies.llm,
        settings=settings,
    )
    destiny.recover_interrupted_journeys()
    settings_journal_key = "settings-transaction:pending"

    settings_update_lock = asyncio.Lock()

    def serialize_settings_update(handler):  # type: ignore[no-untyped-def]
        @wraps(handler)
        async def guarded(*args, **kwargs):  # type: ignore[no-untyped-def]
            async with settings_update_lock:
                return await handler(*args, **kwargs)

        return guarded

    def recover_settings_transaction(journal: dict[str, Any]) -> None:
        previous_config = journal.get("previous_config")
        previous_profile = journal.get("previous_profile")
        transaction_id = str(journal.get("transaction_id") or "").strip()
        if not isinstance(previous_config, dict) or not isinstance(previous_profile, dict):
            raise RuntimeError("settings recovery journal is malformed")
        if transaction_id and len(transaction_id) < 12:
            raise RuntimeError("settings recovery journal has an invalid transaction id")
        container.config.restore(previous_config)
        profile_update = journal.get("profile_update")
        current_profile = container.profiles.load_document("user_profile")
        current_identity = current_profile.get("identity")
        if isinstance(profile_update, dict):
            previous_name = str(profile_update.get("previous_preferred_name") or "")
            applied_name = str(profile_update.get("applied_preferred_name") or "")
            if (
                applied_name
                and isinstance(current_identity, dict)
                and str(current_identity.get("preferred_name") or "") == applied_name
            ):
                # Preserve concurrent edits to other profile fields and only
                # compensate the one field this settings transaction owns.
                profile_snapshot = deepcopy(current_profile)
                profile_snapshot["identity"]["preferred_name"] = previous_name
                profile_snapshot.pop("revision", None)
                profile_snapshot.pop("updated_at", None)
                container.profiles.save_document("user_profile", profile_snapshot)
        elif int(current_profile.get("revision", 0)) == int(previous_profile.get("revision", 0)) + 1:
            # Pre-transaction-ID journals do not identify the changed field.
            # Their snapshot is safe only when no later profile write occurred.
            profile_snapshot = deepcopy(previous_profile)
            profile_snapshot.pop("revision", None)
            profile_snapshot.pop("updated_at", None)
            container.profiles.save_document("user_profile", profile_snapshot)
        container.conversation.refresh_language_model()
        destiny.llm = container.conversation.dependencies.llm

    pending_settings = container.database.get_document(settings_journal_key)
    if isinstance(pending_settings, dict):
        try:
            recover_settings_transaction(pending_settings)
        except Exception as exc:  # noqa: BLE001 - mixed model/settings state must not be hidden
            raise RuntimeError("Mindspace 必须先恢复上一次未完成的设置修改") from exc
        container.database.delete_document(settings_journal_key)
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
        container.art_catalog.close()

    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.audio = audio
    app.state.destiny = destiny

    web_root = STATIC_APP_ROOT
    avatar_root = settings.runtime_dir / "data" / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)
    avatar_config_path = avatar_root / "config.json"
    character_root = settings.runtime_dir / "data" / "characters"
    character_root.mkdir(parents=True, exist_ok=True)
    scene_asset_root = settings.runtime_dir / "data" / "assets" / "scenes"
    scene_asset_root.mkdir(parents=True, exist_ok=True)

    destiny_avatar_prefix = "/api/v1/avatar/files/"

    def destiny_avatar_path(src: object) -> Path | None:
        """Return a safe local path only for journey-scoped avatar resources."""
        if not isinstance(src, str) or not src.startswith(destiny_avatar_prefix):
            return None
        filename = src.removeprefix(destiny_avatar_prefix)
        if not filename.startswith("destiny-") or Path(filename).name != filename:
            return None
        candidate = (avatar_root / filename).resolve()
        return candidate if candidate.parent == avatar_root.resolve() else None

    def destiny_avatar_is_referenced(filename: str) -> bool:
        src = f"{destiny_avatar_prefix}{filename}"
        for _key, journey in container.database.list_documents("destiny-journey:"):
            if isinstance(journey, dict) and journey.get("seed", {}).get("avatar", {}).get("src") == src:
                return True
        return any(
            isinstance(record.get("avatar"), dict) and record["avatar"].get("src") == src
            for record in container.characters.list(include_archived=True)
        )

    def cleanup_unreferenced_destiny_avatars(*, minimum_age_seconds: int = 3600) -> None:
        cutoff = time.time() - minimum_age_seconds
        for candidate in avatar_root.glob("destiny-*"):
            if not candidate.is_file() or candidate.stat().st_mtime > cutoff:
                continue
            if not destiny_avatar_is_referenced(candidate.name):
                candidate.unlink(missing_ok=True)

    def bind_destiny_avatar(journey: dict[str, Any]) -> dict[str, Any]:
        """Turn an upload preview into a journey-owned immutable snapshot."""
        seed = journey.get("seed") if isinstance(journey.get("seed"), dict) else {}
        avatar = deepcopy(seed.get("avatar") or {})
        src = str(avatar.get("src") or "")
        prefix = f"{destiny_avatar_prefix}destiny-upload-"
        if not src.startswith(prefix):
            return journey
        filename = src.removeprefix(destiny_avatar_prefix)
        if Path(filename).name != filename:
            raise ValueError("命格头像地址无效，请重新上传头像。")
        source = (avatar_root / filename).resolve()
        if source.parent != avatar_root.resolve() or not source.is_file():
            raise ValueError("命格头像暂存已失效，请重新上传头像。")
        journey_id = str(journey["journey_id"])
        target_name = f"destiny-journey-{journey_id}-{uuid4().hex}{source.suffix.lower()}"
        target = avatar_root / target_name
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(source.read_bytes())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        source.unlink(missing_ok=True)
        avatar["src"] = f"{destiny_avatar_prefix}{target_name}"
        updated = deepcopy(journey)
        updated.setdefault("seed", {})["avatar"] = avatar
        container.database.put_document(destiny._key(journey_id), updated)
        return updated

    def promote_destiny_avatar(record: dict[str, Any]) -> dict[str, Any]:
        """Move a committed journey avatar into its character-owned directory."""
        avatar = deepcopy(record.get("avatar") or {})
        source = destiny_avatar_path(avatar.get("src"))
        if source is None:
            return record
        if not source.is_file():
            raise ValueError("命格头像文件已不存在，请重新上传头像后再收入角色库")

        character_id = str(record["character_id"])
        target_dir = character_root / character_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"avatar{source.suffix.lower()}"
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(source.read_bytes())
            temporary.replace(target)
            avatar["src"] = f"/api/v1/character/files/{character_id}/{target.name}"
            updated = container.characters.update(
                character_id,
                {"revision": record["revision"], "avatar": avatar},
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        source.unlink(missing_ok=True)
        return updated

    cleanup_unreferenced_destiny_avatars()
    art_pack_root = settings.runtime_dir / "data" / "assets" / "packs"
    art_pack_root.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    app.mount("/api/v1/avatar/files", StaticFiles(directory=avatar_root), name="avatars")
    app.mount(
        "/api/v1/character/files",
        StaticFiles(directory=character_root),
        name="character-files",
    )
    app.mount(
        "/api/v1/art/files",
        StaticFiles(directory=art_pack_root),
        name="art-pack-files",
    )
    app.mount(
        "/api/v1/scene/files",
        StaticFiles(directory=scene_asset_root),
        name="scene-files",
    )

    context = ApiContext(
        container=container,
        settings=settings,
        audio=audio,
        destiny=destiny,
        shared_http=shared_http,
        web_root=web_root,
        avatar_root=avatar_root,
        avatar_config_path=avatar_config_path,
        character_root=character_root,
        scene_asset_root=scene_asset_root,
        settings_journal_key=settings_journal_key,
        serialize_settings_update=serialize_settings_update,
        recover_settings_transaction=recover_settings_transaction,
        destiny_avatar_is_referenced=destiny_avatar_is_referenced,
        cleanup_unreferenced_destiny_avatars=cleanup_unreferenced_destiny_avatars,
        bind_destiny_avatar=bind_destiny_avatar,
        promote_destiny_avatar=promote_destiny_avatar,
    )
    register_system_routes(app, context)
    register_legacy_routes(app, context)
    register_destiny_routes(app, context)
    register_character_routes(app, context)
    register_audio_scene_routes(app, context)
    register_chat_routes(app, context)
    register_memory_routes(app, context)
    return app


app: FastAPI


def __getattr__(name: str):
    """Lazily provide ``mindspace_graph.api:app`` without a second eager container."""

    if name != "app":
        raise AttributeError(name)
    application = create_app()
    globals()["app"] = application
    return application


__all__ = ["create_app", "app", "_character_summary", "_voice_energy_threshold_db"]
