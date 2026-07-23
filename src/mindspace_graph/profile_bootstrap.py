"""Deterministic three-turn profile bootstrap detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY, MemoryField
from mindspace_graph.models import ChatRequest, ProfileBundle

BOOTSTRAP_EMPTY_THRESHOLD = 0.30
BOOTSTRAP_MAX_ROUNDS = 3
BOOTSTRAP_MAX_FIELDS = 8
BOOTSTRAP_MAX_LEAF_PATCHES = 24


def _read_pointer(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for token in pointer.strip("/").split("/"):
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return not value
    return False


@dataclass(frozen=True, slots=True)
class ProfileBootstrap:
    active: bool
    round_index: int
    empty_ratio: float
    total_fields: int
    empty_field_codes: frozenset[str]
    eligible_fields: tuple[MemoryField, ...]
    allowed_evidence: dict[str, frozenset[str]]
    evidence_sources: dict[str, str]
    max_fields: int = BOOTSTRAP_MAX_FIELDS
    max_leaf_patches: int = BOOTSTRAP_MAX_LEAF_PATCHES

    @classmethod
    def inactive(cls, *, round_index: int = 0, empty_ratio: float = 0) -> ProfileBootstrap:
        return cls(
            active=False,
            round_index=round_index,
            empty_ratio=empty_ratio,
            total_fields=0,
            empty_field_codes=frozenset(),
            eligible_fields=(),
            allowed_evidence={},
            evidence_sources={},
        )


def evaluate_profile_bootstrap(
    request: ChatRequest,
    profiles: ProfileBundle,
    history: list[dict[str, Any]],
    *,
    has_pending_deletions: bool = False,
) -> ProfileBootstrap:
    """Create a fill-only contract; the model never chooses whether it is enabled."""

    completed_turns = sum(1 for item in history if item.get("role") == "user")
    round_index = completed_turns + 1
    persistent_fields = tuple(
        field
        for field in DEFAULT_MEMORY_REGISTRY.fields
        if field.target in {"user_profile", "ai_profile"} and field.lifecycle == "persistent"
    )
    documents = {
        "user_profile": profiles.user_profile,
        "ai_profile": profiles.ai_profile,
    }
    empty_fields = tuple(
        field
        for field in persistent_fields
        if _is_empty(_read_pointer(documents[field.target], field.path))
    )
    ratio = len(empty_fields) / max(1, len(persistent_fields))

    within_window = (
        request.mode == "primary"
        and not request.initiative
        and 1 <= request.round <= BOOTSTRAP_MAX_ROUNDS
        and 1 <= round_index <= BOOTSTRAP_MAX_ROUNDS
        and not has_pending_deletions
    )
    has_user_setup = bool(request.user_persona.strip()) or request.user_name.strip() not in {
        "",
        "用户",
    }
    has_character_setup = bool(
        request.system_prompt.strip()
    ) or request.character_name.strip() not in {"", "AI助手", "Mindspace"}

    eligible: list[MemoryField] = []
    evidence: dict[str, frozenset[str]] = {}
    for field in empty_fields:
        if field.target == "ai_profile":
            if not has_character_setup:
                continue
            allowed = frozenset({"character_setup"})
        else:
            allowed_values = {"current_user"}
            if has_user_setup:
                allowed_values.add("user_setup")
            allowed = frozenset(allowed_values)
        eligible.append(field)
        evidence[field.field_code] = allowed

    active = within_window and ratio >= BOOTSTRAP_EMPTY_THRESHOLD and bool(eligible)
    return ProfileBootstrap(
        active=active,
        round_index=round_index,
        empty_ratio=ratio,
        total_fields=len(persistent_fields),
        empty_field_codes=frozenset(field.field_code for field in empty_fields),
        eligible_fields=tuple(eligible) if active else (),
        allowed_evidence=evidence if active else {},
        evidence_sources=(
            {
                "current_user": request.message.strip(),
                "user_setup": "\n".join(
                    value
                    for value in (request.user_name.strip(), request.user_persona.strip())
                    if value
                ),
                "character_setup": "\n".join(
                    value
                    for value in (request.character_name.strip(), request.system_prompt.strip())
                    if value
                ),
            }
            if active
            else {}
        ),
    )
