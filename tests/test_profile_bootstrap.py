from __future__ import annotations

from copy import deepcopy

from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.models import ChatRequest, JsonPatch, JsonUpdatePlan, ProfileBundle
from mindspace_graph.policies import (
    normalize_json_update,
    sanitize_profile_bootstrap,
    validate_json_update,
)
from mindspace_graph.profile_bootstrap import evaluate_profile_bootstrap
from mindspace_graph.prompting import build_messages


def profiles() -> ProfileBundle:
    return ProfileBundle(
        user_profile=deepcopy(DEFAULT_PROFILES["user_profile"]),
        ai_profile=deepcopy(DEFAULT_PROFILES["ai_profile"]),
        runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
        revisions={"user_profile": 0, "ai_profile": 0, "runtime_state": 0},
    )


def request(*, round_num: int = 1) -> ChatRequest:
    return ChatRequest(
        message="你好，我想先认识你。",
        session_id="bootstrap",
        round=round_num,
        user_name="林澈",
        user_persona="林澈是成年音效设计师，慢热，重视边界。",
        character_name="弦月",
        system_prompt=(
            "弦月温柔、敏锐、坦率，习惯直接沟通并尊重用户边界；冲突时先倾听，修复时明确道歉并调整。"
        ),
    )


def test_bootstrap_is_server_enabled_only_in_first_three_turns_when_sparse():
    bundle = profiles()
    first = evaluate_profile_bootstrap(request(), bundle, [])

    assert first.active is True
    assert first.round_index == 1
    assert first.empty_ratio >= 0.30
    assert any(field.target == "ai_profile" for field in first.eligible_fields)
    assert any(field.target == "user_profile" for field in first.eligible_fields)

    fourth = evaluate_profile_bootstrap(request(round_num=4), bundle, [])
    completed_three = evaluate_profile_bootstrap(
        request(round_num=3),
        bundle,
        [{"role": "user"}, {"role": "user"}, {"role": "user"}],
    )
    pending_delete = evaluate_profile_bootstrap(request(), bundle, [], has_pending_deletions=True)

    assert fourth.active is False
    assert completed_three.active is False
    assert pending_delete.active is False


def test_bootstrap_accepts_up_to_eight_fill_only_setup_patches():
    bundle = profiles()
    bootstrap = evaluate_profile_bootstrap(request(), bundle, [])
    candidate = JsonUpdatePlan(
        base_revisions=bundle.revisions,
        trigger="profile_bootstrap",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="add",
                path=path,
                value=value,
                evidence_ids=["character_setup"],
            )
            for path, value in (
                ("/relationship_rules/preferred_interactions", "直接沟通"),
                ("/relationship_rules/conflict_behavior", "先倾听"),
                ("/relationship_rules/repair_behavior", "明确道歉并调整"),
                ("/behavior_rules/hard_boundaries", "尊重用户边界"),
            )
        ],
    )

    normalized = normalize_json_update(candidate, bundle)
    result = validate_json_update(normalized, bundle, bootstrap=bootstrap)

    assert len(normalized.patches) == 4
    assert result.is_valid is True


def test_bootstrap_deterministically_drops_paraphrases_not_present_in_setup():
    bundle = profiles()
    bootstrap = evaluate_profile_bootstrap(request(), bundle, [])
    candidate = JsonUpdatePlan(
        base_revisions=bundle.revisions,
        trigger="profile_bootstrap",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="add",
                path="/relationship_rules/conflict_behavior/-",
                value="先倾听",
                evidence_ids=["character_setup"],
            ),
            JsonPatch(
                target="ai_profile",
                op="add",
                path="/relationship_rules/preferred_interactions/-",
                value="每天主动问候",
                evidence_ids=["character_setup"],
            ),
        ],
    )

    sanitized = sanitize_profile_bootstrap(candidate, bootstrap)

    assert [patch.value for patch in sanitized.patches] == ["先倾听"]
    assert validate_json_update(sanitized, bundle, bootstrap=bootstrap).is_valid is True


def test_bootstrap_rejects_overwrite_and_closes_on_fourth_round():
    bundle = profiles()
    active = evaluate_profile_bootstrap(request(), bundle, [])
    overwrite = JsonUpdatePlan(
        base_revisions=bundle.revisions,
        trigger="profile_bootstrap",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="replace",
                path="/identity/name",
                value="另一个名字",
                evidence_ids=["character_setup"],
            )
        ],
    )

    assert validate_json_update(overwrite, bundle, bootstrap=active).is_valid is False

    closed = evaluate_profile_bootstrap(request(round_num=4), bundle, [])
    empty_field_patch = JsonUpdatePlan(
        base_revisions=bundle.revisions,
        trigger="profile_bootstrap",
        patches=[
            JsonPatch(
                target="ai_profile",
                op="add",
                path="/relationship_rules/preferred_interactions/-",
                value="直接沟通",
                evidence_ids=["character_setup"],
            )
        ],
    )
    assert validate_json_update(empty_field_patch, bundle, bootstrap=closed).is_valid is False


def test_prompt_exposes_bootstrap_only_during_active_window():
    bundle = profiles()
    active = evaluate_profile_bootstrap(request(), bundle, [])
    active_messages = build_messages(request(), bundle, [], [], [], active)
    closed_messages = build_messages(request(round_num=4), bundle, [], [], [])

    active_prompt = "\n".join(item["content"] for item in active_messages)
    closed_prompt = "\n".join(item["content"] for item in closed_messages)
    active_system = "\n".join(
        item["content"] for item in active_messages if item["role"] == "system"
    )

    assert "人物档案初始化窗口" in active_prompt
    assert "trigger=profile_bootstrap" in active_prompt
    assert "最多补充 8 个不同字段" in active_prompt
    assert "总叶子 Patch 不得超过 24 个" in active_prompt
    assert "field_code" not in active_prompt
    assert "profile_bootstrap" not in closed_prompt
    assert "人物档案初始化窗口" not in active_system
