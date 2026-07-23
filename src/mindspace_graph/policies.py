"""Deterministic trust, retrieval, and JSON write policies."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY, MemoryField
from mindspace_graph.models import (
    ChatRequest,
    JsonPatch,
    JsonUpdatePlan,
    JsonUpdateValidation,
    ProfileBundle,
    RetrievedChunk,
)
from mindspace_graph.profile_bootstrap import ProfileBootstrap

MAX_PATCHES_PER_TURN = 3
REVISION_KEYS = {"user_profile", "ai_profile", "runtime_state"}


def rank_with_temporal_decay(
    chunks: list[RetrievedChunk], request: ChatRequest, *, limit: int | None = None
) -> list[RetrievedChunk]:
    """Apply decay, bounded starvation protection, and per-memory-family diversity."""

    settings = request.retrieval
    now = datetime.now(UTC)
    ranked: list[RetrievedChunk] = []
    for chunk in chunks:
        temporal_weight = 1.0
        if settings.temporal_enabled:
            delta_round = abs(request.round - chunk.round_num)
            round_weight = math.exp(-delta_round / settings.decay_rounds)
            hour_weight = 1.0
            if chunk.physical_time:
                try:
                    then = datetime.fromisoformat(chunk.physical_time.replace("Z", "+00:00"))
                    if then.tzinfo is None:
                        then = then.replace(tzinfo=UTC)
                    hours = max(0.0, (now - then.astimezone(UTC)).total_seconds() / 3600)
                    hour_weight = math.exp(-hours / settings.decay_hours)
                except ValueError:
                    hour_weight = 1.0
            temporal_weight = round_weight * hour_weight
        metadata = dict(chunk.metadata)
        base_score = chunk.score * temporal_weight
        starvation_bonus = 0.0
        if settings.fairness_enabled and chunk.source == "memory":
            misses = max(0, int(metadata.get("eligible_misses", 0)))
            last_selected = max(0, int(metadata.get("last_selected_round", 0)))
            stale_rounds = (
                max(0, request.round - last_selected - settings.starvation_rounds)
                if last_selected
                else max(0, misses - settings.starvation_rounds)
            )
            pressure = misses / max(1, settings.starvation_rounds) + stale_rounds * 0.25
            starvation_bonus = min(settings.starvation_boost, pressure * 0.02)
        metadata["base_weighted_score"] = base_score
        metadata["starvation_bonus"] = starvation_bonus
        ranked.append(
            chunk.model_copy(
                update={
                    "metadata": metadata,
                    "temporal_weight": temporal_weight,
                    "weighted_score": base_score + starvation_bonus,
                }
            )
        )
    ordered = sorted(ranked, key=lambda item: item.weighted_score, reverse=True)
    if limit is None or len(ordered) <= limit:
        return ordered if limit is None else ordered[:limit]
    if not settings.fairness_enabled:
        return ordered[:limit]

    reserve = min(limit, round(limit * settings.low_exposure_ratio))
    if reserve == 0 and settings.low_exposure_ratio > 0:
        reserve = 1
    primary_slots = max(0, limit - reserve)
    selected: list[RetrievedChunk] = []
    selected_ids: set[str] = set()
    family_counts: dict[str, int] = {}

    def can_take(item: RetrievedChunk) -> bool:
        if item.source != "memory":
            return True
        family = str(item.metadata.get("memory_family") or item.chunk_id)
        return family_counts.get(family, 0) < settings.memory_family_limit

    def take(item: RetrievedChunk) -> None:
        selected.append(item)
        selected_ids.add(item.chunk_id)
        if item.source == "memory":
            family = str(item.metadata.get("memory_family") or item.chunk_id)
            family_counts[family] = family_counts.get(family, 0) + 1

    for item in ordered:
        if len(selected) >= primary_slots:
            break
        if can_take(item):
            take(item)

    protected = sorted(
        (
            item
            for item in ordered
            if item.chunk_id not in selected_ids
            and item.source == "memory"
            and (
                int(item.metadata.get("eligible_misses", 0)) > 0
                or (
                    int(item.metadata.get("last_selected_round", 0)) > 0
                    and request.round - int(item.metadata["last_selected_round"])
                    >= settings.starvation_rounds
                )
            )
        ),
        key=lambda item: (
            int(item.metadata.get("eligible_misses", 0)),
            request.round - int(item.metadata.get("last_selected_round", 0)),
            float(item.metadata.get("base_weighted_score", 0)),
        ),
        reverse=True,
    )
    for item in protected:
        if len(selected) >= limit:
            break
        if can_take(item):
            take(item)

    # Empty protected slots are returned to relevance ranking. The family cap is
    # relaxed only after all diverse candidates have had an opportunity.
    for item in ordered:
        if len(selected) >= limit:
            break
        if item.chunk_id not in selected_ids and can_take(item):
            take(item)
    for item in ordered:
        if len(selected) >= limit:
            break
        if item.chunk_id not in selected_ids:
            take(item)
    return selected


def _document_for(profiles: ProfileBundle, target: str) -> dict[str, Any]:
    return {
        "user_profile": profiles.user_profile,
        "ai_profile": profiles.ai_profile,
        "runtime_state": profiles.runtime_state,
    }[target]


def _list_patch_location(patch: JsonPatch, field: MemoryField) -> tuple[str, str] | None:
    prefix = f"{field.path}/"
    if not patch.path.startswith(prefix):
        return None
    suffix = patch.path.removeprefix(prefix)
    return (field.path, suffix) if suffix == "-" or suffix.isdigit() else None


def _lookup(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for token in pointer.strip("/").split("/") if pointer != "/" else []:
        token = token.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _same_value(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(
        right, ensure_ascii=False, sort_keys=True
    )


def _same_memory_entity(
    left: Any,
    right: Any,
    field: MemoryField,
    entities: EntityRegistry | None,
) -> bool:
    if _same_value(left, right):
        return True
    if entities is None or field.value_kind != "list":
        return False
    entity_type = field.conflict_group or field.field_code
    left_id = entities.resolve(left, scope=field.scope, entity_type=entity_type, create=False)
    right_id = entities.resolve(right, scope=field.scope, entity_type=entity_type, create=False)
    return bool(left_id and right_id and left_id == right_id)


def normalize_json_update(
    plan: JsonUpdatePlan,
    profiles: ProfileBundle,
    entities: EntityRegistry | None = None,
) -> JsonUpdatePlan:
    """Convert model-friendly field operations into strict leaf JSON Patch operations."""

    normalized: list[JsonPatch] = []
    for patch in plan.patches:
        field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
        if field is None:
            normalized.append(patch)
            continue
        if field.value_kind == "scalar":
            normalized.append(
                patch.model_copy(update={"op": "replace"})
                if patch.path == field.path and patch.op == "add"
                else patch
            )
            continue
        if patch.path != field.path:
            normalized.append(patch)
            continue
        try:
            current = list(_lookup(_document_for(profiles, patch.target), field.path))
        except (KeyError, TypeError, ValueError):
            normalized.append(patch)
            continue
        raw_values = patch.value if isinstance(patch.value, list) else [patch.value]
        desired = [value for value in raw_values if value is not None]
        if patch.op == "remove":
            wanted = desired
            for index in range(len(current) - 1, -1, -1):
                if any(
                    _same_memory_entity(current[index], value, field, entities) for value in wanted
                ):
                    normalized.append(
                        patch.model_copy(update={"path": f"{field.path}/{index}", "value": None})
                    )
            continue
        if patch.op == "replace":
            for index in range(len(current) - 1, -1, -1):
                if not any(
                    _same_memory_entity(current[index], value, field, entities) for value in desired
                ):
                    normalized.append(
                        patch.model_copy(
                            update={
                                "op": "remove",
                                "path": f"{field.path}/{index}",
                                "value": None,
                            }
                        )
                    )
        for value in desired:
            if not any(_same_memory_entity(item, value, field, entities) for item in current):
                normalized.append(
                    patch.model_copy(
                        update={"op": "add", "path": f"{field.path}/-", "value": value}
                    )
                )
    # Opposing sets share an entity identity. Adding an alias to one polarity
    # deterministically removes the same entity from every peer polarity.
    expanded: list[JsonPatch] = []
    for patch in normalized:
        field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
        if field and patch.op == "add" and field.reducer == "opposing_set" and field.conflict_group:
            for peer in DEFAULT_MEMORY_REGISTRY.fields:
                if (
                    peer.target != field.target
                    or peer.field_code == field.field_code
                    or peer.conflict_group != field.conflict_group
                ):
                    continue
                try:
                    peer_values = list(_lookup(_document_for(profiles, peer.target), peer.path))
                except (KeyError, TypeError, ValueError):
                    continue
                for index in range(len(peer_values) - 1, -1, -1):
                    if _same_memory_entity(peer_values[index], patch.value, peer, entities):
                        expanded.append(
                            patch.model_copy(
                                update={
                                    "op": "remove",
                                    "path": f"{peer.path}/{index}",
                                    "value": None,
                                }
                            )
                        )
        expanded.append(patch)
    normalized = expanded
    trigger = plan.trigger
    if plan.patches and not normalized:
        trigger = "none"
    return plan.model_copy(update={"trigger": trigger, "patches": normalized})


def _source_supports(value: Any, evidence: set[str], bootstrap: ProfileBootstrap) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    needle = " ".join(value.split()).casefold()
    return any(
        needle in " ".join(bootstrap.evidence_sources.get(item, "").split()).casefold()
        for item in evidence
    )


def sanitize_profile_bootstrap(
    plan: JsonUpdatePlan,
    bootstrap: ProfileBootstrap | None,
) -> JsonUpdatePlan:
    """Drop bootstrap leaves that cannot be deterministically traced to setup text."""

    if plan.trigger != "profile_bootstrap":
        return plan
    bootstrap = bootstrap or ProfileBootstrap.inactive()
    if not bootstrap.active:
        return plan
    accepted: list[JsonPatch] = []
    accepted_fields: set[str] = set()
    for patch in plan.patches:
        field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
        if field is None or field.field_code not in bootstrap.empty_field_codes:
            continue
        evidence = set(patch.evidence_ids)
        allowed = bootstrap.allowed_evidence.get(field.field_code, frozenset())
        if patch.op == "remove" or not evidence or evidence - allowed:
            continue
        if not _source_supports(patch.value, evidence, bootstrap):
            continue
        if field.field_code not in accepted_fields and len(accepted_fields) >= bootstrap.max_fields:
            continue
        if len(accepted) >= bootstrap.max_leaf_patches:
            break
        accepted.append(patch)
        accepted_fields.add(field.field_code)
    return plan.model_copy(
        update={
            "trigger": "profile_bootstrap" if accepted else "none",
            "patches": accepted,
        }
    )


def _validate_path_and_operation(patch: JsonPatch, profiles: ProfileBundle) -> str | None:
    if patch.path == "/identity/gender":
        return (
            f"{patch.target}: gender is user-owned and may only be changed "
            "through a direct profile edit"
        )
    field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
    if field is None:
        return f"{patch.target}: path not allowed or not a leaf: {patch.path}"
    if field.value_kind == "scalar":
        if patch.path != field.path:
            return f"{patch.target}: invalid scalar path: {patch.path}"
        if patch.op == "add":
            return f"{patch.target}: scalar fields must use replace: {patch.path}"
        if patch.op == "remove":
            return f"{patch.target}: required scalar field cannot be removed: {patch.path}"
        return None

    location = _list_patch_location(patch, field)
    if location is None:
        return f"{patch.target}: path not allowed or not a leaf: {patch.path}"
    base, suffix = location
    try:
        current = _lookup(_document_for(profiles, patch.target), base)
    except (KeyError, IndexError, TypeError, ValueError):
        return f"{patch.target}: list path does not exist: {base}"
    if not isinstance(current, list):
        return f"{patch.target}: expected list at {base}"
    if patch.op == "add":
        if suffix != "-" and int(suffix) > len(current):
            return f"{patch.target}: add index out of range: {patch.path}"
    elif suffix == "-" or int(suffix) >= len(current):
        return f"{patch.target}: index out of range: {patch.path}"
    return None


def validate_json_update(
    plan: JsonUpdatePlan,
    profiles: ProfileBundle,
    *,
    pending_deletion_ids: set[str] | None = None,
    bootstrap: ProfileBootstrap | None = None,
    current_response: str = "",
    current_user: str = "",
) -> JsonUpdateValidation:
    errors: list[str] = []
    pending_deletion_ids = pending_deletion_ids or set()
    bootstrap = bootstrap or ProfileBootstrap.inactive()

    if set(plan.base_revisions) != REVISION_KEYS:
        errors.append("base_revisions must contain exactly user_profile, ai_profile, runtime_state")
    for key in REVISION_KEYS:
        expected = plan.base_revisions.get(key)
        current = profiles.revisions.get(key)
        if expected != current:
            errors.append(f"stale revision for {key}: expected {expected}, current {current}")

    patch_limit = (
        bootstrap.max_leaf_patches if plan.trigger == "profile_bootstrap" else MAX_PATCHES_PER_TURN
    )
    if len(plan.patches) > patch_limit:
        errors.append(f"at most {patch_limit} JSON patches are allowed per turn")
    if plan.trigger == "none" and plan.patches:
        errors.append("trigger=none requires patches=[]")
    if plan.trigger != "none" and not plan.patches:
        errors.append(f"trigger={plan.trigger} requires at least one patch")
    if plan.trigger == "deletion_reconciliation" and not pending_deletion_ids:
        errors.append("deletion_reconciliation requires a pending deletion event")
    if plan.trigger == "profile_bootstrap" and not bootstrap.active:
        errors.append("profile_bootstrap is not active for this turn")

    for patch in plan.patches:
        evidence = set(patch.evidence_ids)
        if plan.trigger == "current_user" and "current_user" not in evidence:
            errors.append(f"{patch.target}: current_user trigger requires current_user evidence")
        if plan.trigger == "current_user" and evidence - {"current_user"}:
            errors.append(f"{patch.target}: history or retrieval cannot be write evidence")
        if plan.trigger == "current_user" and current_user and patch.op != "remove":
            if not isinstance(patch.value, str) or not patch.value.strip():
                errors.append(f"{patch.target}: extracted current_user value must be text")
            else:
                source = re.sub(r"\s+", "", current_user).casefold()
                candidate = re.sub(r"\s+", "", patch.value).casefold()
                if candidate not in source:
                    errors.append(
                        f"{patch.target}: extracted value is absent from current_user"
                    )
        if plan.trigger == "current_agent":
            field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
            if evidence != {"current_response"}:
                errors.append(
                    f"{patch.target}: current_agent requires only current_response evidence"
                )
            if field is None or field.scope != "agent":
                errors.append(
                    f"{patch.target}: current_agent may only update registered agent fields"
                )
            if patch.op == "remove":
                errors.append(f"{patch.target}: current_agent cannot remove fields")
            if not isinstance(patch.value, str) or not patch.value.strip():
                errors.append(f"{patch.target}: current_agent value must be non-empty text")
            else:
                source = re.sub(r"\s+", "", current_response).casefold()
                candidate = re.sub(r"\s+", "", patch.value).casefold()
                if candidate not in source:
                    errors.append(
                        f"{patch.target}: current_agent value is absent from current_response"
                    )
        if plan.trigger == "deletion_reconciliation":
            deletion_evidence = evidence & pending_deletion_ids
            if not deletion_evidence and "current_user" not in evidence:
                errors.append(f"{patch.target}: patch lacks a current deletion or user evidence id")
            unknown = evidence - pending_deletion_ids - {"current_user"}
            if unknown:
                labels = ", ".join(sorted(unknown))
                errors.append(f"{patch.target}: unknown deletion evidence: {labels}")
        if plan.trigger == "profile_bootstrap":
            field = DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path)
            field_code = field.field_code if field else ""
            allowed = bootstrap.allowed_evidence.get(field_code, frozenset())
            if field_code not in bootstrap.empty_field_codes:
                errors.append(f"{patch.target}: bootstrap may only fill empty fields: {patch.path}")
            if not evidence or evidence - allowed:
                errors.append(
                    f"{patch.target}: invalid bootstrap evidence for "
                    f"{patch.path}: {sorted(evidence)}"
                )
            if patch.op == "remove":
                errors.append(f"{patch.target}: bootstrap cannot remove fields: {patch.path}")
            if not _source_supports(patch.value, evidence, bootstrap):
                errors.append(
                    f"{patch.target}: bootstrap value is not present in its source: {patch.path}"
                )
        path_error = _validate_path_and_operation(patch, profiles)
        if path_error:
            errors.append(path_error)
        if patch.op != "remove" and patch.value is None:
            errors.append(f"{patch.target}: {patch.op} requires a non-null value at {patch.path}")

    if plan.trigger == "deletion_reconciliation" and not any(
        set(patch.evidence_ids) & pending_deletion_ids for patch in plan.patches
    ):
        errors.append("deletion_reconciliation must reference at least one pending deletion event")
    if plan.trigger == "profile_bootstrap":
        setup_ids = {"user_setup", "character_setup"}
        distinct_fields = {
            field.field_code
            for patch in plan.patches
            if (field := DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path))
        }
        if len(distinct_fields) > bootstrap.max_fields:
            errors.append(
                f"profile_bootstrap allows at most {bootstrap.max_fields} distinct fields"
            )
        if not any(set(patch.evidence_ids) & setup_ids for patch in plan.patches):
            errors.append("profile_bootstrap must use at least one configured setup source")
        current_only_fields = {
            field.field_code
            for patch in plan.patches
            if set(patch.evidence_ids) == {"current_user"}
            if (field := DEFAULT_MEMORY_REGISTRY.resolve(patch.target, patch.path))
        }
        if len(current_only_fields) > MAX_PATCHES_PER_TURN:
            errors.append(
                f"profile_bootstrap allows at most {MAX_PATCHES_PER_TURN} current-user-only fields"
            )

    return JsonUpdateValidation(
        is_valid=not errors,
        errors=errors,
        normalized_plan=plan if not errors else None,
    )
