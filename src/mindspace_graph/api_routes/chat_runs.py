"""Chat, durable run, interruption, and session routes."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from mindspace_graph.api_contracts.chat import ChatTurnCreateRequest
from mindspace_graph.models import ChatResponse

from .context import ApiContext, InterruptRequest, SessionCreateRequest, _character_summary


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container
    audio = context.audio

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
                role_state=container.conversation.session_role_state(payload.character_id),
            )
            container.characters.touch(payload.character_id)
            return session
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(payload: ChatTurnCreateRequest, x_request_id: str | None = Header(default=None)):
        request = payload.to_internal()
        try:
            return await container.conversation.invoke(request, x_request_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/v1/chat/stream")
    async def chat_stream(
        payload: ChatTurnCreateRequest,
        x_request_id: str | None = Header(default=None),
    ):
        request = payload.to_internal()
        try:
            request_id = await container.conversation.prepare_stream(request, x_request_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(
            container.conversation.stream(request, request_id),
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
        session["messages"] = [item for item in session.get("messages", []) if not item.get("hidden")]
        character_id = str(session.get("character_id") or "")
        if character_id:
            try:
                session["character"] = _character_summary(container.characters.get(character_id))
            except KeyError:
                session["character_missing"] = True
        return session

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        with container.database.transaction(operation="delete_session", details={"session_id": session_id}):
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
        with container.database.transaction(operation="clear_session", details={"session_id": session_id}):
            if not container.sessions.clear_session(session_id):
                raise HTTPException(status_code=404, detail="session not found")
            container.memory.forget_session(session_id)
            container.context.delete_session(session_id)
        return {"success": True}

    @app.get("/api/v1/sessions/{session_id}/context-diagnostics")
    async def context_diagnostics(session_id: str):
        return container.context.diagnostics(session_id)


__all__ = ["register_routes"]
