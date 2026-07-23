"""Dormant emotion adapter kept behind the public port for future reactivation."""

from __future__ import annotations

from typing import Any


class DisabledEmotionCoordinator:
    """No-op implementation that performs no model loading, I/O, or background work."""

    def enabled(self) -> bool:
        return False

    def previous_for_round(self, _session_id: str, _round_num: int) -> None:
        return None

    def schedule_post_turn(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def close(self) -> None:
        return None
