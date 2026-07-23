"""Mindspace package with lazy exports for lightweight audio workers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mindspace_graph.models import ChatRequest, ChatResponse

__all__ = ["ChatRequest", "ChatResponse", "build_graph"]


def __getattr__(name: str) -> Any:
    if name == "build_graph":
        from mindspace_graph.graph import build_graph

        return build_graph
    if name in {"ChatRequest", "ChatResponse"}:
        from mindspace_graph import models

        return getattr(models, name)
    raise AttributeError(name)
