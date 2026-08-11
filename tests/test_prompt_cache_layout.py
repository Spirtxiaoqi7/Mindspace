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
from mindspace_graph.native_tools import NATIVE_TOOL_GUIDANCE
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
        character_id="cache-layout-character",
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
        character_id="cache-layout-character",
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
    snapshot_contents = [item["content"] for item in round_thirteen.context_snapshot.messages]
    assert any("用户第10轮" in value for value in snapshot_contents)
    assert any("角色第12轮" in value for value in snapshot_contents)
    assert not any("用户第9轮" in value for value in snapshot_contents)
    assert not any("角色第9轮" in value for value in snapshot_contents)
    assert (
        round_thirteen.messages[: len(round_thirteen.context_snapshot.prefix_messages)]
        == round_thirteen.context_snapshot.prefix_messages
    )
    restored_contents = [item["content"] for item in round_thirteen.context_snapshot.messages]
    assert not any("【低可信召回】" in value for value in restored_contents)
    assert not any("【本轮可用工具、Skill 与 MCP】" in value for value in restored_contents)
    diagnostics = ledger.diagnostics("cache-layout")
    assert diagnostics["event_count"] > diagnostics["model_visible_event_count"]


def test_compatibility_split_keeps_exactly_the_latest_three_rounds():
    base, tail = split_history_for_cache(history_through(15), 16)
    assert {item["round"] for item in base} == {13, 14, 15}
    assert len(base) == 6
    assert tail == []


def test_hidden_initiative_trigger_is_not_persisted_as_dialogue_history(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    proactive = ChatRequest(
        message="服务端主动续话触发占位",
        session_id="initiative-history",
        character_id="initiative-character",
        round=1,
        initiative=True,
        initiative_trigger="idle_continuation",
    )
    built = build_prompt(
        proactive,
        bundle,
        [],
        [],
        [],
        context_ledger=ledger,
    )
    assert built.context_snapshot is not None
    ledger.append_turn(
        request_id="initiative-run",
        session_id="initiative-history",
        character_id="initiative-character",
        round_num=1,
        epoch_id=built.context_snapshot.epoch_id,
        pending_events=built.pending_events,
        response="角色主动说出的可见正文",
        user_message_id="hidden-u1",
        assistant_message_id="a1",
        receipt=JsonWriteReceipt(turn_id="round_1"),
        profiles=bundle,
    )

    next_turn = build_prompt(
        ChatRequest(message="继续", session_id="initiative-history", character_id="initiative-character", round=2),
        bundle,
        [],
        [],
        [],
        context_ledger=ledger,
    )
    history_text = "\n".join(item["content"] for item in next_turn.context_snapshot.messages)
    assert "服务端主动续话触发占位" not in history_text
    assert "角色主动说出的可见正文" in history_text


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
    built = build_prompt(
        request(12),
        profiles(),
        history_through(11),
        retrieval,
        [],
        tool_hint="memory",
        context_ledger=ContextLedger(tmp_path / "context.db"),
    )
    contents = [item["content"] for item in built.messages]

    json_index = next(index for index, value in enumerate(contents) if "【权威 JSON 基线】" in value)
    history_index = next(index for index, value in enumerate(contents) if "用户第9轮" in value)
    retrieval_index = next(index for index, value in enumerate(contents) if "【低可信召回】" in value)
    input_index = next(index for index, value in enumerate(contents) if "【当前用户明确输入】" in value)
    calibration_index = next(index for index, value in enumerate(contents) if value.startswith("已确认状态："))

    assert json_index < retrieval_index < history_index < input_index
    assert input_index < calibration_index
    assert not any(item["kind"] == "tool_context" for item in built.pending_events)
    assert calibration_index == len(built.messages) - 1
    assert built.messages[-1]["role"] == "system"


def test_native_tool_guidance_is_short_and_old_protocol_is_absent() -> None:
    built = build_prompt(
        request(1),
        profiles(),
        [],
        [],
        [],
        tool_hint="web",
        native_tools_enabled=True,
    )
    text = "\n".join(item["content"] for item in built.messages)
    assert NATIVE_TOOL_GUIDANCE in text
    assert "零调用提示=web" in text
    assert "knowledge.search_local" not in text
    assert "capability_plan" not in text


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
    retrieval = next(item for item in built.pending_events if item["kind"] == "retrieval_context")

    assert '"chunk_id":"a2"' not in retrieval["content"]
    assert '"chunk_id":"knowledge-1"' in retrieval["content"]
    assert retrieval["metadata"]["deduplicated_chunk_count"] == 1


def test_old_raw_chat_can_return_through_rag_outside_direct_history_window():
    history = history_through(12)
    context = [
        RetrievedChunk(
            chunk_id="u1",
            text="用户第1轮",
            source="chat",
            score=0.95,
            weighted_score=0.95,
        ),
        RetrievedChunk(
            chunk_id="a12",
            text="角色第12轮",
            source="chat",
            score=0.9,
            weighted_score=0.9,
        ),
    ]

    built = build_prompt(request(13), profiles(), history, context, [])
    retrieval = next(item for item in built.pending_events if item["kind"] == "retrieval_context")

    assert '"chunk_id":"u1"' in retrieval["content"]
    assert '"chunk_id":"a12"' not in retrieval["content"]
    assert retrieval["metadata"]["deduplicated_chunk_count"] == 1


def test_adult_roleplay_context_activates_profile_rules_in_final_calibration():
    bundle = profiles()
    bundle.ai_profile["behavior_rules"]["contextual_rules"] = ["仅在 R18 情境中启用的角色规则"]
    bundle.ai_profile["relationship_rules"]["preferred_interactions"] = ["NSFW续写"]
    built = build_prompt(
        ChatRequest(
            message="不会的，来吧。",
            session_id="adult-roleplay",
            round=3,
            adult_mode=True,
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
    assert "仅在 R18 情境中启用的角色规则" not in calibration["content"]
    assert "NSFW续写" not in calibration["content"]
    assert "你正在进行成人向角色扮演" in calibration["content"]
    assert "自然而清晰地描写身体与性行为" in calibration["content"]
    event = built.pending_events[-1]
    assert event["kind"] == "roleplay_post_history"
    assert event["ephemeral"] is True
    assert event["persistence_eligible"] is False


def test_r18_sexual_action_mode_is_explicit_and_does_not_replace_normal_adult_detection():
    built = build_prompt(
        ChatRequest(
            message="继续",
            session_id="r18-enhanced",
            round=4,
            adult_mode=True,
            user_name="林澈",
            character_name="弦月",
        ),
        profiles(),
        [],
        [],
        [],
    )

    calibration = built.messages[-1]
    assert "你正在进行成人向角色扮演" in calibration["content"]
    assert "鸡巴、阴茎、龟头" in calibration["content"]
    assert "指代清晰" in calibration["content"]
    assert "本轮质量目标" not in calibration["content"]
    turn_control = next(item for item in built.pending_events if item["kind"] == "turn_control")
    assert turn_control["metadata"]["adult_mode"] is True


def test_r18_final_directive_is_compact_and_does_not_force_escalation():
    built = build_prompt(
        ChatRequest(
            message="继续",
            session_id="r18-foreplay-loop",
            round=4,
            adult_mode=True,
            user_name="林澈",
            character_name="弦月",
        ),
        profiles(),
        [
            {"role": "assistant", "round": 2, "content": "我吻住你，手指慢慢解开衣扣。"},
            {"role": "assistant", "round": 3, "content": "我贴得更近，问你准备好了吗？"},
        ],
        [],
        [],
    )

    calibration = built.messages[-1]["content"]
    assert "最近 R18 推进状态" not in calibration
    assert "当前 R18 Director" not in calibration
    assert "六级" not in calibration
    assert "自然而清晰地描写身体与性行为" in calibration
    assert "不要跳出扮演进行解释" in calibration


def test_private_r18_output_protocol_is_not_injected_into_generation():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["r18_protocol"] = [
        "原文规则A：必须保留这个句子。",
        "原文规则B：符号♡与括号()也保持。",
    ]
    adult = build_prompt(
        ChatRequest(
            message="继续",
            session_id="r18-private-protocol",
            round=2,
            adult_mode=True,
        ),
        bundle,
        [],
        [],
        [],
    )
    ordinary = build_prompt(
        ChatRequest(
            message="继续",
            session_id="r18-private-protocol-off",
            round=2,
            adult_mode=False,
        ),
        bundle,
        [],
        [],
        [],
    )

    adult_text = adult.messages[-1]["content"]
    ordinary_text = "\n".join(item["content"] for item in ordinary.messages)
    assert "你正在进行成人向角色扮演" in adult_text
    assert "原文规则A：必须保留这个句子。" not in adult_text
    assert "原文规则B：符号♡与括号()也保持。" not in adult_text
    assert "原文规则A：必须保留这个句子。" not in ordinary_text
    assert "你正在进行成人向角色扮演" not in ordinary_text


def test_gender_identity_is_the_first_high_priority_system_content():
    bundle = profiles()
    bundle.user_profile["identity"]["gender"] = "男"
    bundle.ai_profile["identity"]["gender"] = "女"

    built = build_prompt(request(1), bundle, [], [], [])

    first = built.messages[0]
    assert first["role"] == "system"
    assert first["content"].startswith("【身份状态】")
    assert "用户性别：男；角色性别：女" in first["content"]
    assert "你是Mindspace" in first["content"]
    assert "【身份与身体一致性】" in first["content"]
    assert "女性角色自身不得出现男性器官或男性生理反应" in first["content"]
    assert "【V2 权威角色档案】" in first["content"]
    assert "【角色扮演合约】" in first["content"]
    assert first["content"].count("【V2 权威角色档案】") == 1


def test_reply_length_is_only_added_from_explicit_user_setting():
    bundle = profiles()
    bundle.user_profile["custom_profile"] = "我喜欢自然简洁的聊天，但没有指定固定篇幅。"
    natural = build_prompt(request(1), bundle, [], [], [])
    natural_text = "\n".join(item["content"] for item in natural.messages)

    assert "【用户设定的回复篇幅】" not in natural_text
    assert "我喜欢自然简洁的聊天，但没有指定固定篇幅。" in natural_text
    assert "target_characters" not in natural_text
    assert "minimum_content_beats" not in natural_text

    explicit_request = request(1).model_copy(update={"reply_length_preference": "日常简洁，重要话题可以自然展开"})
    explicit = build_prompt(explicit_request, bundle, [], [], [])
    explicit_text = "\n".join(item["content"] for item in explicit.messages)

    assert "【用户设定的回复篇幅】\n日常简洁，重要话题可以自然展开" in explicit_text


def test_r18_uses_only_the_explicit_length_preference_without_a_separate_quota():
    bundle = profiles()
    text_request = request(1).model_copy(
        update={
            "adult_mode": True,
            "interaction_mode": "text",
            "reply_length_preference": "本轮约五百字",
        }
    )
    text_prompt = "\n".join(item["content"] for item in build_prompt(text_request, bundle, [], [], []).messages)

    assert "【R18 文字节奏】" not in text_prompt
    assert "约 300 个汉字" not in text_prompt
    assert "【用户设定的回复篇幅】\n本轮约五百字" in text_prompt

    voice_request = text_request.model_copy(update={"interaction_mode": "voice"})
    voice_prompt = "\n".join(item["content"] for item in build_prompt(voice_request, bundle, [], [], []).messages)

    assert "【R18 文字节奏】" not in voice_prompt


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

    scene_event = next(item for item in built.pending_events if item["kind"] == "voice_face_to_face_context")
    scene_index = next(
        index for index, item in enumerate(built.messages) if "【面对面互动一级规则】" in item["content"]
    )
    assert scene_index >= 3
    assert scene_event["role"] == "system"
    assert scene_event["ephemeral"] is True
    assert scene_event["persistence_eligible"] is False
    assert scene_event["metadata"]["eligible_for_json_evidence"] is False
