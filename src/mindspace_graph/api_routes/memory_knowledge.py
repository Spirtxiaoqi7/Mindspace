"""Structured memory, entity registry, chat chunks, and knowledge routes."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY

from .context import (
    ApiContext,
    EntityAliasRequest,
    EntityMergeRequest,
    EntityRequest,
    KnowledgeRequest,
    MemoryKeyRequest,
    MemoryRebuildRequest,
    MemoryValueRequest,
)


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container

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
        entity_id = container.entities.resolve(payload.value, scope=payload.scope, entity_type=payload.entity_type)
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
        items = container.memory_service.list_items(include_history=include_history, character_id=character_id)
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
            **container.memory_service.rebuild(dry_run=payload.dry_run, character_id=character_id),
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


__all__ = ["register_routes"]
