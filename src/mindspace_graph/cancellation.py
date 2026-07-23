"""Thread-safe cancellation state shared by HTTP handlers and graph nodes."""

from __future__ import annotations

from threading import RLock


class GenerationCancelled(RuntimeError):
    """Raised before side effects when a user interrupts a turn."""


class CancellationRegistry:
    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._active: set[str] = set()
        self._lock = RLock()

    def start(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.discard(request_id)
            self._active.add(request_id)

    def cancel(self, request_id: str) -> bool:
        if not request_id:
            return False
        with self._lock:
            self._cancelled.add(request_id)
        return True

    def is_cancelled(self, request_id: str) -> bool:
        if not request_id:
            return False
        with self._lock:
            return request_id in self._cancelled

    def finish(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.discard(request_id)
            self._active.discard(request_id)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def raise_if_cancelled(self, request_id: str) -> None:
        if self.is_cancelled(request_id):
            raise GenerationCancelled(f"generation cancelled: {request_id}")
