"""Compatibility tombstones for removed HTTP APIs."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .context import ApiContext


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    del context

    @app.get("/api/v1/characters/options")
    async def legacy_character_options():
        raise HTTPException(
            status_code=410,
            detail="旧角色档案选项已废弃，请从命格系统创建 V2 角色卡",
        )

    @app.post("/api/v1/destiny/journeys/{journey_id}/cards/{archetype_id}")
    async def legacy_generate_destiny_cards(journey_id: str, archetype_id: str):
        del journey_id, archetype_id
        raise HTTPException(status_code=410, detail="旧版逐角色命签接口已废弃，请使用全量 cards 接口")

    @app.get(
        "/api/v1/characters/fate-options",
        operation_id="legacy_fate_options_get",
    )
    @app.post(
        "/api/v1/characters/fate-options",
        operation_id="legacy_fate_options_post",
    )
    async def legacy_fate_options():
        raise HTTPException(status_code=410, detail="旧版命格选项接口已废弃，请使用 V7 命格旅程接口")

    @app.get(
        "/api/v1/character-drafts",
        operation_id="legacy_character_drafts_collection_get",
    )
    @app.post(
        "/api/v1/character-drafts",
        operation_id="legacy_character_drafts_collection_post",
    )
    @app.put(
        "/api/v1/character-drafts",
        operation_id="legacy_character_drafts_collection_put",
    )
    @app.patch(
        "/api/v1/character-drafts",
        operation_id="legacy_character_drafts_collection_patch",
    )
    @app.delete(
        "/api/v1/character-drafts",
        operation_id="legacy_character_drafts_collection_delete",
    )
    @app.get(
        "/api/v1/character-drafts/{legacy_path:path}",
        operation_id="legacy_character_drafts_path_get",
    )
    @app.post(
        "/api/v1/character-drafts/{legacy_path:path}",
        operation_id="legacy_character_drafts_path_post",
    )
    @app.put(
        "/api/v1/character-drafts/{legacy_path:path}",
        operation_id="legacy_character_drafts_path_put",
    )
    @app.patch(
        "/api/v1/character-drafts/{legacy_path:path}",
        operation_id="legacy_character_drafts_path_patch",
    )
    @app.delete(
        "/api/v1/character-drafts/{legacy_path:path}",
        operation_id="legacy_character_drafts_path_delete",
    )
    async def legacy_character_drafts(legacy_path: str = ""):
        del legacy_path
        raise HTTPException(
            status_code=410,
            detail="旧角色档案、蓝图和系统提示词创建链已废弃，请使用 V7 命格生成 V2 角色卡",
        )


__all__ = ["register_routes"]
