"""Compatibility exports for legacy file-storage repository imports."""

from mindspace_graph.adapters.profile_repository import (
    DEFAULT_PROFILES,
    TARGET_FILES,
    JsonProfileRepository,
)
from mindspace_graph.adapters.session_repository import JsonSessionRepository
from mindspace_graph.infrastructure.storage.json_io import atomic_json_write
from mindspace_graph.infrastructure.storage.json_patch import (
    apply_json_patch,
    json_pointer_tokens,
    read_json_pointer,
)

_atomic_json = atomic_json_write
_apply_patch = apply_json_patch
_pointer_tokens = json_pointer_tokens
_read_pointer = read_json_pointer

__all__ = [
    "DEFAULT_PROFILES",
    "TARGET_FILES",
    "JsonProfileRepository",
    "JsonSessionRepository",
]
