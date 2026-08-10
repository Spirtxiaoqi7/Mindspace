"""Durable conversation-run storage, SSE envelopes, and subscriber recovery."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mindspace_graph.models import ChatRequest, ChatResponse
from mindspace_graph.product_database import ProductDatabase


@dataclass(slots=True)
class StreamEnvelopeFactory:
    """Build monotonically sequenced SSE envelopes for one conversation run."""

    run_id: str
    session_id: str
    round: int
    sequence: int = 0

    def sse(self, event: str, data: dict[str, Any] | None = None) -> str:
        self.sequence += 1
        payload = {
            "version": "1.0",
            "event": event,
            "seq": self.sequence,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "round": self.round,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data or {},
        }
        return f"id: {self.sequence}\nevent: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class BufferedStreamRun:
    """A turn-owned event log that survives individual HTTP subscribers."""

    def __init__(self, request_id: str, request: ChatRequest) -> None:
        self.request_id = request_id
        self.request = request
        self.events: deque[tuple[int, str]] = deque(maxlen=8192)
        self.condition = asyncio.Condition()
        self.completed = False
        self.terminal_event = ""
        self.updated_at = time.monotonic()
        self.task: asyncio.Task[None] | None = None
        self.partial_text = ""
        self.last_checkpoint_at = time.monotonic()
        self.last_checkpoint_size = 0
        self.response: ChatResponse | None = None
        self.error = ""
        self.provider_attempts: list[dict[str, Any]] = []


RunProducer = Callable[[BufferedStreamRun], Awaitable[None]]


class ConversationRunRepository:
    """Own in-process runs and their durable SQLite event representation."""

    def __init__(self, database: ProductDatabase | None) -> None:
        self.database = database
        self.active_runs: dict[str, BufferedStreamRun] = {}
        self.lock = asyncio.Lock()
        if database is not None:
            database.recover_interrupted_runs()
            database.prune_conversation_runs(retention_hours=24)

    async def ensure(
        self,
        request: ChatRequest,
        request_id: str,
        producer: RunProducer,
    ) -> BufferedStreamRun:
        """Create or join one run while binding its id to one normalized turn."""

        async with self.lock:
            request_digest = request.idempotency_digest()
            now = time.monotonic()
            expired = [
                key
                for key, value in self.active_runs.items()
                if value.completed and now - value.updated_at > 600
            ]
            for key in expired:
                self.active_runs.pop(key, None)

            existing = self.active_runs.get(request_id)
            if existing is not None:
                if (
                    existing.request.session_id != request.session_id
                    or existing.request.round != request.round
                    or existing.request.idempotency_digest() != request_digest
                ):
                    raise ValueError("request id is already bound to a different request")
                return existing

            if self.database is not None:
                previous = self.database.get_conversation_run(request_id)
                durable = self.database.create_conversation_run(
                    run_id=request_id,
                    session_id=request.session_id,
                    round_num=request.round,
                    request_digest=request_digest,
                )
                if previous is not None:
                    if str(durable.get("status")) == "running":
                        self.database.interrupt_orphaned_conversation_run(request_id)
                        durable = self.database.get_conversation_run(request_id) or durable
                    restored = self.restore(request, durable)
                    self.active_runs[request_id] = restored
                    return restored

            run = BufferedStreamRun(request_id, request)
            self.active_runs[request_id] = run
            run.task = asyncio.create_task(producer(run), name=f"mindspace-run-{request_id[:12]}")
            return run

    async def resume(self, request_id: str, *, after_sequence: int = 0) -> AsyncIterator[str]:
        """Resume an active subscriber or replay a durable terminal event log."""

        async with self.lock:
            run = self.active_runs.get(request_id)
        if run is None:
            if self.database is None or self.database.get_conversation_run(request_id) is None:
                raise KeyError(request_id)
            for payload in self.database.conversation_run_events(request_id, after_sequence):
                yield payload
            return
        async for event in self.subscribe(run, after_sequence):
            yield event

    async def status(self, request_id: str) -> dict[str, Any] | None:
        """Return the same status shape for active and durable-only runs."""

        async with self.lock:
            run = self.active_runs.get(request_id)
        if run is None:
            record = self.database.get_conversation_run(request_id) if self.database is not None else None
            if record is None:
                return None
            return {
                "run_id": request_id,
                "completed": record["status"] != "running",
                "status": record["status"],
                "terminal_event": record["terminal_event"],
                "latest_seq": record["latest_seq"],
                "partial_text": record["partial_text"],
                "session_id": record["session_id"],
                "round": record["round_num"],
            }
        return {
            "run_id": request_id,
            "completed": run.completed,
            "terminal_event": run.terminal_event,
            "latest_seq": run.events[-1][0] if run.events else 0,
        }

    def restore(self, request: ChatRequest, durable: dict[str, Any]) -> BufferedStreamRun:
        """Rehydrate a terminal run without re-executing model or tools."""

        run = BufferedStreamRun(str(durable["run_id"]), request)
        payloads = self.database.conversation_run_events(run.request_id, 0) if self.database is not None else []
        for payload in payloads:
            sequence, event, data = decode_sse(payload)
            run.events.append((sequence, payload))
            if event == "model.attempt":
                run.provider_attempts.append(dict(data))
            if event in {"run.completed", "run.error"} and isinstance(data.get("response"), dict):
                run.response = ChatResponse.model_validate(data["response"])
            if event == "run.error":
                run.error = str(data.get("error") or "durable run failed")
        run.partial_text = str(durable.get("partial_text") or "")
        run.completed = str(durable.get("status")) != "running"
        run.terminal_event = str(durable.get("terminal_event") or "")
        run.updated_at = time.monotonic()
        return run

    async def publish(
        self,
        run: BufferedStreamRun,
        sequence: int,
        payload: str,
        *,
        terminal: str = "",
    ) -> None:
        """Publish one event to subscribers and its durable replay log."""

        _sequence, event_name, event_data = decode_sse(payload)
        async with run.condition:
            run.events.append((sequence, payload))
            run.updated_at = time.monotonic()
            if event_name == "response.delta":
                run.partial_text += str(event_data.get("delta") or "")
            elif event_name == "response.replace":
                run.partial_text = str(event_data.get("content") or "")
            elif event_name == "model.attempt":
                run.provider_attempts.append(dict(event_data))
            if terminal:
                run.completed = True
                run.terminal_event = terminal
            run.condition.notify_all()

        if self.database is None:
            return
        now = time.monotonic()
        checkpoint_due = (
            now - run.last_checkpoint_at >= 0.5
            or len(run.partial_text) - run.last_checkpoint_size >= 1024
            or bool(terminal)
        )
        if checkpoint_due:
            self.database.checkpoint_conversation_run(run.request_id, run.partial_text, sequence)
            run.last_checkpoint_at = now
            run.last_checkpoint_size = len(run.partial_text)
        if event_name != "response.delta":
            self.database.append_conversation_run_event(
                run_id=run.request_id,
                sequence=sequence,
                event=event_name or "graph.event",
                payload=payload,
                terminal=bool(terminal),
            )

    async def subscribe(self, run: BufferedStreamRun, after_sequence: int) -> AsyncIterator[str]:
        """Replay after a cursor, then follow events until the terminal event."""

        cursor = max(0, int(after_sequence))
        while True:
            heartbeat = False
            async with run.condition:
                available = [item for item in run.events if item[0] > cursor]
                completed = run.completed
                if not available and not completed:
                    try:
                        await asyncio.wait_for(run.condition.wait(), timeout=15.0)
                    except TimeoutError:
                        heartbeat = True
                    available = [item for item in run.events if item[0] > cursor]
                    completed = run.completed
            if heartbeat and not available:
                yield ": heartbeat\n\n"
                continue
            for sequence, payload in available:
                cursor = max(cursor, sequence)
                yield payload
            if completed and not [item for item in run.events if item[0] > cursor]:
                return


def decode_sse(payload: str) -> tuple[int, str, dict[str, Any]]:
    """Decode the sequence, event name, and data object from one SSE payload."""

    sequence = 0
    event = ""
    data: dict[str, Any] = {}
    for line in payload.splitlines():
        if line.startswith("id:"):
            try:
                sequence = int(line.partition(":")[2].strip())
            except ValueError:
                sequence = 0
        elif line.startswith("event:"):
            event = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            try:
                envelope = json.loads(line.partition(":")[2].strip())
            except json.JSONDecodeError:
                continue
            raw_data = envelope.get("data") if isinstance(envelope, dict) else None
            data = raw_data if isinstance(raw_data, dict) else {}
    return sequence, event, data


__all__ = [
    "BufferedStreamRun",
    "ConversationRunRepository",
    "StreamEnvelopeFactory",
    "decode_sse",
]
