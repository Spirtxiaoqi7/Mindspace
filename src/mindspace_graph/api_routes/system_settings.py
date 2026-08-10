"""System, public configuration, settings, diagnostics, and data routes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .context import ApiContext, ClearDataRequest


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container
    settings = context.settings
    audio = context.audio
    destiny = context.destiny
    shared_http = context.shared_http
    web_root = context.web_root
    settings_journal_key = context.settings_journal_key
    serialize_settings_update = context.serialize_settings_update
    recover_settings_transaction = context.recover_settings_transaction

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

    @app.get("/api/v1/models/available")
    async def get_available_models():
        base_url = settings.llm_base_url.rstrip("/")
        if not base_url:
            raise HTTPException(status_code=422, detail="请先配置当前 API URL")
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        try:
            response = await shared_http.get(f"{base_url}/models", headers=headers, timeout=12.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"读取当前 API 模型列表失败：{exc}") from exc
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = sorted({str(item.get("id") or "").strip() for item in rows if isinstance(item, dict)} - {""})
        current = settings.llm_model
        if current and current not in model_ids:
            model_ids.insert(0, current)
        return {"base_url": base_url, "current": current, "models": model_ids}

    @app.patch("/api/v1/settings")
    @app.put("/api/v1/settings")
    @serialize_settings_update
    async def put_settings(payload: dict[str, Any]):
        previous_config = container.config.checkpoint()
        previous_runtime_secrets = container.config.runtime_secret_checkpoint()
        previous_profile = container.profiles.load_document("user_profile")
        journal = {
            "schema_version": "1.0.0",
            "revision": 1,
            "state": "prepared",
            "transaction_id": uuid4().hex,
            "previous_config": previous_config,
            "previous_profile": previous_profile,
        }
        container.database.put_document(settings_journal_key, journal)
        try:
            result = container.config.update(payload)
            persona = result.get("persona") if isinstance(result.get("persona"), dict) else {}
            configured_name = str(persona.get("user_name") or "").strip()
            if configured_name:
                user_profile = container.profiles.load_document("user_profile")
                identity = user_profile.setdefault("identity", {})
                if str(identity.get("preferred_name") or "") != configured_name:
                    previous_name = str(identity.get("preferred_name") or "")
                    identity["preferred_name"] = configured_name
                    saved_profile = container.profiles.save_document("user_profile", user_profile)
                    journal["profile_update"] = {
                        "previous_preferred_name": previous_name,
                        "applied_preferred_name": configured_name,
                        "applied_revision": int(saved_profile.get("revision", 0)),
                    }
                    container.database.put_document(settings_journal_key, journal)
            container.conversation.refresh_language_model()
            # Destiny keeps a concrete model instance for its staged generation
            # workflow, so it must follow the live settings refresh as well.
            destiny.llm = container.conversation.dependencies.llm
            container.database.delete_document(settings_journal_key)
            return {"success": True, "settings": result}
        except Exception as exc:
            journal.update({"revision": 2, "state": "rollback_pending", "error": str(exc)[:1000]})
            container.database.put_document(settings_journal_key, journal)
            try:
                recover_settings_transaction(journal)
                container.config.restore_runtime_secrets(previous_runtime_secrets)
            except Exception as rollback_exc:  # noqa: BLE001 - journal remains for startup recovery
                raise HTTPException(
                    status_code=500,
                    detail=f"设置修改失败，自动恢复也失败；重启 Core 会继续恢复：{rollback_exc}",
                ) from rollback_exc
            container.database.delete_document(settings_journal_key)
            status = 422 if isinstance(exc, (TypeError, ValueError)) else 500
            raise HTTPException(status_code=status, detail=str(exc)) from exc

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


__all__ = ["register_routes"]
