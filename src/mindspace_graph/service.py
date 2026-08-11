"""Backward-compatible imports for the application service and composition root."""

from mindspace_graph.application.conversation import ConversationService
from mindspace_graph.bootstrap import ProductContainer, build_container

__all__ = ["ConversationService", "ProductContainer", "build_container"]
