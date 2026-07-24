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


def test_json_baseline_precedes_history_and_post_history_calibration_is_last(tmp_path):
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
        index for index, value in enumerate(contents) if "【本轮能力状态】" in value
    )
    input_index = next(
        index for index, value in enumerate(contents) if "【当前用户明确输入】" in value
    )
    calibration_index = next(
        index for index, value in enumerate(contents) if "【本轮角色演绎校准｜最后执行】" in value
    )

    assert json_index < history_index < retrieval_index < tool_index < input_index
    assert input_index < calibration_index
    assert tool_index == len(built.messages) - 3
    assert calibration_index == len(built.messages) - 1
    assert built.messages[-1]["role"] == "system"


def test_recent_raw_chat_is_not_duplicated_inside_retrieval_context():
    history = history_through(2)
    context = [
        RetrievedChunk(
            chunk_id="a2",
            text="角色第2轮",
            source="chat",
            score=0.9,
            weighted_score=0.9,
        ),
        RetrievedChunk(
            chunk_id="knowledge-1",
            text="只出现一次的知识资料",
            source="knowledge",
            score=0.8,
            weighted_score=0.8,
        ),
    ]

    built = build_prompt(request(3), profiles(), history, context, [])
    retrieval = next(
        item for item in built.pending_events if item["kind"] == "retrieval_context"
    )

    assert '"chunk_id":"a2"' not in retrieval["content"]
    assert '"chunk_id":"knowledge-1"' in retrieval["content"]
    assert retrieval["metadata"]["deduplicated_chunk_count"] == 1


def test_adult_roleplay_context_activates_profile_rules_in_final_calibration():
    bundle = profiles()
    bundle.ai_profile["behavior_rules"]["contextual_rules"] = [
        "仅在 R18 情境中启用的角色规则"
    ]
    bundle.ai_profile["relationship_rules"]["preferred_interactions"] = ["NSFW续写"]
    built = build_prompt(
        ChatRequest(
            message="不会的，来吧。",
            session_id="adult-roleplay",
            round=3,
            interaction_mode="voice",
            user_name="林澈",
            character_name="弦月",
            voice_context={"mode": "face_to_face", "scene": "两人在卧室延续亲密互动"},
        ),
        bundle,
        [
            {"message_id": "u2", "role": "user", "round": 2, "content": "继续刚才的亲密场景"},
            {"message_id": "a2", "role": "assistant", "round": 2, "content": "我已经主动靠近你。"},
        ],
        [],
        [],
    )

    calibration = built.messages[-1]
    assert calibration["role"] == "system"
    assert "成人/亲密情境规则的启用条件" in calibration["content"]
    assert "明确继续信号" in calibration["content"]
    assert "不要再次询问同一个选择" in calibration["content"]
    event = built.pending_events[-1]
    assert event["kind"] == "roleplay_post_history"
    assert event["ephemeral"] is True
    assert event["persistence_eligible"] is False


def test_gender_identity_is_the_first_high_priority_system_content():
    bundle = profiles()
    bundle.user_profile["identity"]["gender"] = "男"
    bundle.ai_profile["identity"]["gender"] = "女"

    built = build_prompt(request(1), bundle, [], [], [])

    first = built.messages[0]
    assert first["role"] == "system"
    assert first["content"].startswith("【最高优先级：第一认同性别】")
    assert "用户的第一认同性别是“男”" in first["content"]
    assert "你的第一认同性别是“女”" in first["content"]
    assert "模型不得自行推断、修改、淡化、重新定义或用其他身份覆盖" in first["content"]


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
