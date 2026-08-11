"""Storage infrastructure shared by concrete repositories."""

from mindspace_graph.infrastructure.storage.json_io import atomic_json_write, read_json
from mindspace_graph.infrastructure.storage.json_patch import (
    apply_json_patch,
    json_pointer_tokens,
    read_json_pointer,
)
from mindspace_graph.infrastructure.storage.metadata import utc_now_iso
from mindspace_graph.infrastructure.storage.paths import (
    hashed_json_document_path,
    legacy_json_document_path,
    safe_json_stem,
)

__all__ = [
    "atomic_json_write",
    "apply_json_patch",
    "json_pointer_tokens",
    "read_json_pointer",
    "hashed_json_document_path",
    "legacy_json_document_path",
    "read_json",
    "safe_json_stem",
    "utc_now_iso",
]
