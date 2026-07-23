from __future__ import annotations

from copy import deepcopy

from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.models import (
    ChatRequest,
    JsonWriteReceipt,
    ProfileBundle,
    RetrievedChunk,
)
from mindspace_graph.prompting import build_prompt, split_history_for_cache


def profiles() -> ProfileBundle:
    return ProfileBundle(
        user_profile=deepcopy(DEFAULT_PROFILES["user_profile"]),
        ai_profile=deepcopy(DEFAULT_PROFILES["ai_profile"]),
        runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
        revisions={"user_profile": 2, "ai_profile": 3, "runtime_state": 4},
    )


def history_through(round_num: int) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for current in range(1, round_num + 1):
        messages.extend(
            [
                {
                    "message_id": f"u{current}",
                    "role": "user",
                    "round": current,
                    "content": f"用户第{current}轮",
                },
                {
                    "message_id": f"a{current}",
                    "role": "assistant",
                    "round": current,
                    "content": f"角色第{current}轮",
                },
            ]
        )
    return messages


def request(round_num: int) -> ChatRequest:
    return ChatRequest(
        message=f"当前第{round_num}轮",
        session_id="cache-layout",
        round=round_num,
        user_name="林澈",
        character_name="弦月",
        system_prompt="保持温柔而坦率。",
        user_persona="成年音效设计师。",
    )


def test_next_turn_keeps_confirmed_messages_but_excludes_audit_context(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    round_twelve = build_prompt(
        request(12),
        bundle,
        history_through(11),
        [],
        [],
        context_ledger=ledger,
    )
    assert round_twelve.context_snapshot is not None
    ledger.append_turn(
        request_id="request-12",
        session_id="cache-layout",
        round_num=12,
        epoch_id=round_twelve.context_snapshot.epoch_id,
        pending_events=round_twelve.pending_events,
        response="角色第12轮",
        user_message_id="u12",
        assistant_message_id="a12",
        receipt=JsonWriteReceipt(turn_id="round_12"),
        profiles=bundle,
    )

    round_thirteen = build_prompt(
        request(13),
        bundle,
        history_through(12),
        [],
        [],
        context_ledger=ledger,
    )
    assert round_thirteen.context_snapshot is not None
    confirmed_user = next(
        {
            "role": str(item["role"]),
            "content": str(item["content"]),
        }
        for item in round_twelve.pending_events
        if item["kind"] == "current_user"
    )
    expected_prefix = [
        *round_twelve.context_snapshot.messages,
        confirmed_user,
        {"role": "assistant", "content": "角色第12轮"},
    ]
    assert round_thirteen.context_snapshot.messages == expected_prefix
    assert round_thirteen.messages[: len(expected_prefix)] == expected_prefix
    restored_contents = [item["content"] for item in round_thirteen.context_snapshot.messages]
    assert not any("【低可信召回】" in value for value in restored_contents)
    assert not any("【本轮可用工具、Skill 与 MCP】" in value for value in restored_contents)
    diagnostics = ledger.diagnostics("cache-layout")
    assert diagnostics["event_count"] > diagnostics["model_visible_event_count"]


def test_compatibility_split_never_performs_a_fixed_five_turn_rebase():
    base, tail = split_history_for_cache(history_through(15), 16)
    assert {item["round"] for item in base} == set(range(1, 16))
    assert tail == []


def test_json_baseline_precedes_history_and_dynamic_tools_remain_at_tail(tmp_path):
    retrieval = [
        RetrievedChunk(
            chunk_id="knowledge-1",
            text="低可信资料",
            source="knowledge",
            score=0.8,
            weighted_score=0.8,
        )
    ]
    tools = [{"name": "memory_lookup", "description": "查询记忆"}]
    built = build_prompt(
        request(12),
        profiles(),
        history_through(11),
        retrieval,
        [],
        available_capabilities=tools,
        context_ledger=ContextLedger(tmp_path / "context.db"),
    )
    contents = [item["content"] for item in built.messages]

    json_index = next(
        index for index, value in enumerate(contents) if "【权威 JSON 基线】" in value
    )
    history_index = next(index for index, value in enumerate(contents) if "用户第1轮" in value)
    retrieval_index = next(
        index for index, value in enumerate(contents) if "【低可信召回】" in value
    )
    tool_index = next(
        index for index, value in enumerate(contents) if "【本轮可用工具、Skill 与 MCP】" in value
    )
    input_index = next(
        index for index, value in enumerate(contents) if "【当前用户明确输入】" in value
    )

    assert json_index < history_index < retrieval_index < tool_index < input_index
    assert tool_index == len(built.messages) - 2


def test_face_to_face_scene_stays_after_the_stable_prefix_and_is_not_persistable():
    built = build_prompt(
        ChatRequest(
            message="继续说",
            session_id="face-scene",
            interaction_mode="voice",
            voice_context={"mode": "face_to_face", "scene": "雨夜客厅"},
        ),
        profiles(),
        [],
        [],
        [],
    )

    scene_event = next(
        item for item in built.pending_events if item["kind"] == "voice_face_to_face_context"
    )
    scene_index = next(
        index
        for index, item in enumerate(built.messages)
        if "【面对面互动一级规则】" in item["content"]
    )
    assert scene_index >= 3
    assert scene_event["role"] == "system"
    assert scene_event["ephemeral"] is True
    assert scene_event["persistence_eligible"] is False
    assert scene_event["metadata"]["eligible_for_json_evidence"] is False
