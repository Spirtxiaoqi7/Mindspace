"""Background retrieval-index warmup coordination for committed chat turns."""

from __future__ import annotations

import asyncio
import time

from mindspace_graph.models import ChatRequest
from mindspace_graph.ports import Dependencies


class RetrievalWarmupCoordinator:
    """Own readiness keys, warmup tasks, audit events, and shutdown draining."""

    def __init__(self, dependencies: Dependencies) -> None:
        self.dependencies = dependencies
        self._retrieval_ready: set[tuple[str, str]] = set()
        self._retrieval_warmups: dict[tuple[str, str], asyncio.Task[None]] = {}

    def is_ready(self, session_id: str, character_id: str) -> bool:
        return (session_id, character_id) in self._retrieval_ready
    def kick(self, request: ChatRequest) -> None:
        """Warm retrieval only after the foreground turn has been committed."""

        if not request.retrieval.rag_enabled:
            return
        key = (request.session_id, request.character_id)
        if key in self._retrieval_ready:
            return
        existing = self._retrieval_warmups.get(key)
        if existing is not None and not existing.done():
            return
        prewarm = getattr(self.dependencies.retriever, "prewarm", None)
        if not callable(prewarm):
            self._retrieval_ready.add(key)
            return
        messages = self.dependencies.sessions.load_all(request.session_id)

        async def worker() -> None:
            started = time.perf_counter()
            self.dependencies.audit.record(
                "retrieval_warmup_started",
                {
                    "session_id": request.session_id,
                    "character_id": request.character_id,
                },
            )
            try:
                details = await asyncio.to_thread(
                    prewarm,
                    session_id=request.session_id,
                    character_id=request.character_id,
                    messages=messages,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - foreground chat already succeeded
                self.dependencies.audit.record(
                    "retrieval_warmup_failed",
                    {
                        "session_id": request.session_id,
                        "character_id": request.character_id,
                        "error": str(exc)[:500],
                    },
                )
            else:
                self._retrieval_ready.add(key)
                self.dependencies.audit.record(
                    "retrieval_warmup_completed",
                    {
                        "session_id": request.session_id,
                        "character_id": request.character_id,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                        "details": details,
                    },
                )
            finally:
                self._retrieval_warmups.pop(key, None)

        self._retrieval_warmups[key] = asyncio.create_task(
            worker(),
            name=f"retrieval-warmup-{request.session_id[:12]}",
        )
    def close(self) -> None:
        for task in self._retrieval_warmups.values():
            task.cancel()

    async def drain(self) -> None:
        """Wait for currently scheduled warmups without exposing task internals."""
        tasks = [task for task in self._retrieval_warmups.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        tasks = [task for task in self._retrieval_warmups.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["RetrievalWarmupCoordinator"]
