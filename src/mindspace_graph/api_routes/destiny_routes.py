"""Destiny journey, avatar, selection, synthesis, and commit routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from mindspace_graph.destiny import DestinySeed, DestinySelectionRequest, public_destiny_definition

from .context import ApiContext, _avatar_suffix


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container
    destiny = context.destiny
    avatar_root = context.avatar_root
    destiny_avatar_is_referenced = context.destiny_avatar_is_referenced
    bind_destiny_avatar = context.bind_destiny_avatar
    promote_destiny_avatar = context.promote_destiny_avatar

    @app.get("/api/v1/destiny/definition")
    async def destiny_definition():
        return public_destiny_definition()

    @app.post("/api/v1/destiny/avatars")
    async def upload_destiny_avatar(file: Annotated[UploadFile, File()]):
        """Store a journey-local avatar without touching global avatar settings."""
        data = await file.read()
        if not data:
            raise HTTPException(status_code=422, detail="头像文件为空，请重新选择 PNG、JPEG、WebP 或 GIF 图片。")
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="头像文件超过 5 MiB，请压缩后再上传。")
        try:
            suffix = _avatar_suffix(file.filename or "avatar.webp", data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = f"destiny-upload-{uuid4().hex}{suffix}"
        target = avatar_root / filename
        temporary = target.with_name(f".{filename}.{uuid4().hex}.tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        return {
            "success": True,
            "avatar": {
                "src": f"/api/v1/avatar/files/{filename}",
                "aspect": "2 / 3",
                "scale": 1.0,
                "x": 0,
                "y": 0,
            },
        }

    @app.delete("/api/v1/destiny/avatars/{filename}")
    async def discard_destiny_avatar(filename: str):
        if not filename.startswith("destiny-upload-") or Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="命格头像不存在")
        if destiny_avatar_is_referenced(filename):
            raise HTTPException(status_code=409, detail="命格头像仍被旅程或角色引用，不能删除")
        (avatar_root / filename).unlink(missing_ok=True)
        return {"success": True}

    @app.post("/api/v1/destiny/journeys")
    async def create_destiny_journey(payload: DestinySeed):
        journey = destiny.create(payload)
        try:
            return bind_destiny_avatar(journey)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/destiny/journeys/{journey_id}/seed")
    async def reset_destiny_seed(
        journey_id: str,
        payload: DestinySeed,
        expected_revision: int | None = Query(default=None),
    ):
        """Replace an unfinished seed without orphaning its prior avatar snapshot."""
        previous = destiny.get(journey_id)
        try:
            updated = destiny.reset_seed(journey_id, payload, expected_revision=expected_revision)
            updated = bind_destiny_avatar(updated)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            status = 409 if "stale" in str(exc) or "不能修改" in str(exc) or "正在生成" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        old_avatar = (previous.get("seed") or {}).get("avatar") if isinstance(previous, dict) else {}
        old_src = old_avatar.get("src") if isinstance(old_avatar, dict) else ""
        new_avatar = (updated.get("seed") or {}).get("avatar") if isinstance(updated, dict) else {}
        new_src = new_avatar.get("src") if isinstance(new_avatar, dict) else ""
        prefix = "/api/v1/avatar/files/"
        old_name = old_src.removeprefix(prefix) if isinstance(old_src, str) and old_src.startswith(prefix) else ""
        should_delete_old = (
            old_name.startswith(f"destiny-journey-{journey_id}-")
            and old_src != new_src
            and not destiny_avatar_is_referenced(old_name)
        )
        if should_delete_old:
            (avatar_root / old_name).unlink(missing_ok=True)
        return updated

    @app.get("/api/v1/destiny/journeys/{journey_id}")
    async def get_destiny_journey(journey_id: str):
        try:
            return destiny.get(journey_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/destiny/journeys/{journey_id}/archetypes")
    async def generate_destiny_archetypes(
        journey_id: str,
        use_default: bool = Query(default=False),
        expected_revision: int | None = Query(default=None),
    ):
        try:
            if use_default:
                return destiny.use_default_archetypes(journey_id, expected_revision=expected_revision)
            return await destiny.generate_archetypes(journey_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TimeoutError, json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            status = 409 if "stale" in str(exc) or "当前旅程不能" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - provider errors are surfaced for retry
            raise HTTPException(status_code=502, detail=f"人物原型生成失败：{exc}") from exc

    @app.post("/api/v1/destiny/journeys/{journey_id}/cards")
    async def generate_destiny_cards(
        journey_id: str,
        use_default: bool = Query(default=False),
        expected_revision: int | None = Query(default=None),
    ):
        try:
            if use_default:
                return destiny.use_default_cards(journey_id, expected_revision=expected_revision)
            return await destiny.generate_cards(journey_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TimeoutError, json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            status = 409 if "stale" in str(exc) or "请先完成" not in str(exc) and "当前旅程不能" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - provider errors are surfaced for retry
            raise HTTPException(status_code=502, detail=f"人物命格拆分失败：{exc}") from exc

    @app.put("/api/v1/destiny/journeys/{journey_id}/selections/{slot_id}")
    async def select_destiny_card(journey_id: str, slot_id: str, payload: DestinySelectionRequest):
        try:
            return destiny.select(journey_id, slot_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            status = 409 if "stale" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/v1/destiny/journeys/{journey_id}/selections/{slot_id}")
    async def unselect_destiny_card(journey_id: str, slot_id: str, expected_revision: int | None = None):
        try:
            return destiny.unselect(journey_id, slot_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            status = 409 if "stale" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post("/api/v1/destiny/journeys/{journey_id}/rewind/archetypes")
    async def rewind_destiny_archetypes(journey_id: str, expected_revision: int | None = None):
        try:
            return destiny.rewind_archetypes(journey_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            status = 409 if "stale" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/v1/destiny/journeys/{journey_id}/selections")
    async def clear_destiny_selections(journey_id: str, expected_revision: int | None = None):
        try:
            return destiny.clear_selections(journey_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            status = 409 if "stale" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post("/api/v1/destiny/journeys/{journey_id}/synthesize")
    async def synthesize_destiny_journey(journey_id: str, expected_revision: int | None = Query(default=None)):
        try:
            return await destiny.synthesize(journey_id, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TimeoutError, json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            status = 409 if "stale" in str(exc) or "当前旅程不能" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - provider errors are surfaced for retry
            raise HTTPException(status_code=502, detail=f"最终人物合成失败：{exc}") from exc

    @app.post("/api/v1/destiny/journeys/{journey_id}/commit")
    async def commit_destiny_journey(journey_id: str, expected_revision: int | None = Query(default=None)):
        try:
            with container.database.transaction(
                operation="api_commit_destiny_journey", details={"journey_id": journey_id}
            ):
                record = destiny.commit(journey_id, expected_revision=expected_revision)
                record = promote_destiny_avatar(record)
                container.memory_service.rebuild(
                    dry_run=False,
                    character_id=str(record["character_id"]),
                )
            return {"success": True, "character": record}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            status = 409 if "stale" in str(exc) or "正在" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc


__all__ = ["register_routes"]
