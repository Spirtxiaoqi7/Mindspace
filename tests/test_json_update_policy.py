from __future__ import annotations

from mindspace_graph.adapters.in_memory import InMemoryProfileRepository
from mindspace_graph.models import JsonPatch, JsonUpdatePlan
from mindspace_graph.policies import normalize_json_update, validate_json_update


def bundle():
    return InMemoryProfileRepository().load_bundle()


def patch(**overrides):
    values = {
        "target": "user_profile",
        "op": "replace",
        "path": "/identity/preferred_name",
        "value": "小林",
        "evidence_ids": ["current_user"],
    }
    values.update(overrides)
    return JsonPatch(**values)


def plan(*patches, trigger="current_user"):
    return JsonUpdatePlan(
        base_revisions=bundle().revisions,
        trigger=trigger,
        patches=list(patches),
    )


def test_server_fields_and_parent_objects_are_never_writable():
    server_field = validate_json_update(
        plan(patch(path="/revision", value=99)),
        bundle(),
    )
    parent_object = validate_json_update(
        plan(patch(path="/identity", value={"preferred_name": "小林"})),
        bundle(),
    )

    assert server_field.is_valid is False
    assert parent_object.is_valid is False


def test_history_and_retrieval_cannot_be_write_evidence():
    result = validate_json_update(
        plan(patch(evidence_ids=["current_user", "chat-7"])),
        bundle(),
    )

    assert result.is_valid is False
    assert any("history or retrieval" in error for error in result.errors)


def test_deletion_reconciliation_requires_a_current_pending_event():
    valid_plan = plan(
        patch(evidence_ids=["delete-1"]),
        trigger="deletion_reconciliation",
    )

    valid = validate_json_update(
        valid_plan,
        bundle(),
        pending_deletion_ids={"delete-1"},
    )
    invalid = validate_json_update(
        valid_plan,
        bundle(),
        pending_deletion_ids={"delete-other"},
    )

    assert valid.is_valid is True
    assert invalid.is_valid is False


def test_stale_revision_blocks_the_whole_update():
    candidate = plan(patch())
    candidate.base_revisions["user_profile"] = 99

    result = validate_json_update(candidate, bundle())

    assert result.is_valid is False
    assert any("stale revision" in error for error in result.errors)


def test_registry_normalizes_model_friendly_scalar_and_list_operations():
    profiles = bundle()
    profiles.user_profile["stable_preferences"] = {
        "likes": ["草莓"],
        "dislikes": [],
    }
    candidate = plan(
        patch(op="add", path="/identity/preferred_name", value="阿澈"),
        patch(op="replace", path="/stable_preferences/likes", value=["蓝莓"]),
    )

    normalized = normalize_json_update(candidate, profiles)

    assert normalized.patches[0].op == "replace"
    assert [(item.op, item.path, item.value) for item in normalized.patches[1:]] == [
        ("remove", "/stable_preferences/likes/0", None),
        ("add", "/stable_preferences/likes/-", "蓝莓"),
    ]
    assert validate_json_update(normalized, profiles).is_valid is True


def test_registry_normalizes_single_value_written_to_a_list_base_path():
    profiles = bundle()
    profiles.runtime_state["user_state"] = {"current_emotional_cues": []}
    candidate = JsonUpdatePlan(
        base_revisions=profiles.revisions,
        trigger="current_user",
        patches=[
            JsonPatch(
                target="runtime_state",
                op="add",
                path="/user_state/current_emotional_cues",
                value="紧张",
                evidence_ids=["current_user"],
            )
        ],
    )

    normalized = normalize_json_update(candidate, profiles)

    assert normalized.patches[0].path == "/user_state/current_emotional_cues/-"
    assert validate_json_update(normalized, profiles).is_valid is True


def test_current_agent_can_write_only_a_directly_spoken_agent_value():
    profiles = bundle()
    profiles.ai_profile["personality"] = {"core_traits": ["可靠"], "speech_style": []}
    candidate = JsonUpdatePlan(
        base_revisions=profiles.revisions,
        trigger="current_agent",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="add",
                path="/personality/core_traits/-",
                value="很容易满足",
                evidence_ids=["current_response"],
            )
        ],
    )

    valid = validate_json_update(
        candidate,
        profiles,
        current_response="嗯，我确实是个很容易满足的人。",
    )
    absent = validate_json_update(
        candidate,
        profiles,
        current_response="我会认真想想。",
    )

    assert valid.is_valid is True
    assert absent.is_valid is False
    assert any("absent from current_response" in error for error in absent.errors)


def test_current_agent_cannot_modify_user_fields_or_remove_memory():
    profiles = bundle()
    profiles.ai_profile["personality"] = {"core_traits": ["可靠"], "speech_style": []}
    user_patch = JsonUpdatePlan(
        base_revisions=profiles.revisions,
        trigger="current_agent",
        patches=[
            JsonPatch(
                target="user_profile",
                op="replace",
                path="/identity/preferred_name",
                value="阿澈",
                evidence_ids=["current_response"],
            )
        ],
    )
    remove_patch = JsonUpdatePlan(
        base_revisions=profiles.revisions,
        trigger="current_agent",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="remove",
                path="/personality/core_traits/0",
                value=None,
                evidence_ids=["current_response"],
            )
        ],
    )

    assert validate_json_update(user_patch, profiles, current_response="阿澈").is_valid is False
    assert validate_json_update(remove_patch, profiles, current_response="可靠").is_valid is False
