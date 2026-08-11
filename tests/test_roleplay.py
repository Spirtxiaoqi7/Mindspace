from __future__ import annotations

import json

from mindspace_graph.adapters.file_storage import (
    DEFAULT_PROFILES,
    JsonProfileRepository,
    JsonSessionRepository,
)
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever
from mindspace_graph.models import ApiConfig, ChatRequest, JsonWriteReceipt, ProfileBundle
from mindspace_graph.prompting import _role_profile, build_prompt
from mindspace_graph.r18_director import ADULT_ROLEPLAY_PROTOCOL
from mindspace_graph.roleplay import (
    allow_raw_chat_retrieval,
    build_presentation_plan,
    build_roleplay_layer,
    effective_roleplay_temperature,
    evaluate_roleplay_quality,
    normalize_presentation_response,
    normalize_voice_response,
    project_history_for_presentation,
    resolve_presentation_mode,
)


def request(message: str, **updates) -> ChatRequest:
    values = {
        "message": message,
        "session_id": "roleplay",
        "round": 3,
        "user_name": "用户",
        "character_name": "角色",
        "api": ApiConfig(),
    }
    values.update(updates)
    return ChatRequest(**values)


def profiles() -> ProfileBundle:
    return ProfileBundle(
        user_profile=DEFAULT_PROFILES["user_profile"],
        ai_profile=DEFAULT_PROFILES["ai_profile"],
        runtime_state=DEFAULT_PROFILES["runtime_state"],
        revisions={"user_profile": 0, "ai_profile": 0, "runtime_state": 0},
    )


def test_profile_v13_initializes_roleplay_sections_without_overwriting(tmp_path):
    repository = JsonProfileRepository(tmp_path / "profiles")
    bundle = repository.load_bundle()

    assert bundle.ai_profile["schema_version"] == "1.3.0"
    assert "roleplay" in bundle.ai_profile
    assert "roleplay_state" in bundle.runtime_state


def test_legacy_profile_migration_preserves_existing_character_content(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    legacy = dict(DEFAULT_PROFILES["ai_profile"])
    legacy.pop("roleplay")
    legacy["schema_version"] = "1.1.0"
    legacy["identity"] = dict(legacy["identity"])
    legacy["identity"]["self_description"] = "用户已经写好的原文，不得覆盖"
    (root / "ai-profile.json").write_text(
        json.dumps(legacy, ensure_ascii=False),
        encoding="utf-8",
    )

    document = JsonProfileRepository(root).load_document("ai_profile")

    assert document["schema_version"] == "1.3.0"
    assert document["identity"]["self_description"] == "用户已经写好的原文，不得覆盖"
    assert document["roleplay"]["examples"]["casual"] == []


def test_raw_assistant_prose_is_never_returned_by_chat_retrieval(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    req = request("我喜欢爵士乐", round=1)
    sessions.persist_turn(
        req,
        "好的，我以后每天都给你煮粥。",
        replace_round=False,
        write_receipt=JsonWriteReceipt(turn_id="round_1"),
    )
    retriever = LocalKnowledgeRetriever(tmp_path / "knowledge.json", sessions)

    chunks = retriever.search_chat("喜欢", "roleplay", 10)

    assert [item.metadata["role"] for item in chunks] == ["user"]
    assert chunks[0].text == "我喜欢爵士乐"


def test_acknowledgement_and_bare_scene_transition_skip_raw_chat_retrieval():
    assert allow_raw_chat_retrieval(request("嗯")) is False
    assert allow_raw_chat_retrieval(request("我到家了")) is False
    assert allow_raw_chat_retrieval(request("我到家了，继续刚才说的电影")) is True


def test_presentation_auto_defaults_to_dialogue_and_routes_explicit_action_to_scene():
    assert resolve_presentation_mode(request("你怎么看这件事？"), []) == "dialogue"
    assert resolve_presentation_mode(request("我走过去抱住你"), []) == "scene"
    assert resolve_presentation_mode(request("我们现在在咖啡馆，你怎么看菜单？"), []) == "scene"
    assert resolve_presentation_mode(request("我把菜单推给你，你怎么看？"), []) == "scene"
    assert resolve_presentation_mode(request("随便聊聊今天"), []) == "dialogue"


def test_roleplay_temperature_is_dynamic_and_never_raises_user_setting():
    assert effective_roleplay_temperature(request("普通聊天"), []) == 0.45
    assert effective_roleplay_temperature(request("我把菜单推给你"), []) == 0.25
    assert effective_roleplay_temperature(request("继续", initiative=True), []) == 0.25
    assert effective_roleplay_temperature(request("继续", adult_mode=True), []) == 0.65
    assert effective_roleplay_temperature(request("普通聊天", api=ApiConfig(temperature=0.15)), []) == 0.15


def test_presentation_override_and_scene_continuation_are_observable():
    history = [
        {
            "role": "assistant",
            "content": "（我推开门。）进来。",
            "presentation_mode": "scene",
        }
    ]
    assert resolve_presentation_mode(request("继续"), history) == "scene"
    assert resolve_presentation_mode(request("继续", presentation_mode="dialogue"), history) == "dialogue"
    assert resolve_presentation_mode(request("说说你的看法", presentation_mode="scene"), history) == "scene"


def test_dialogue_projection_drops_only_legacy_stage_openers_and_preserves_normal_parentheses():
    history = [
        {"role": "user", "content": "我走到窗边。"},
        {"role": "assistant", "content": "（我放下杯子。）这事我不赞同。"},
        {"role": "assistant", "content": "我更倾向前一种（但不是绝对）。"},
    ]
    dialogue = project_history_for_presentation(history, "dialogue")
    scene = project_history_for_presentation(history, "scene")
    assert dialogue[0]["content"] == "我走到窗边。"
    assert dialogue[1]["content"] == "这事我不赞同。"
    assert dialogue[2]["content"] == "我更倾向前一种（但不是绝对）。"
    assert scene[1]["content"].startswith("（我放下杯子。）")


def test_presentation_plan_limits_questions_and_scopes_character_agency():
    history = [
        {"role": "assistant", "content": "（我低头看着杯子。）你今天开心吗？"},
        {"role": "assistant", "content": "（我低头转着杯子。）那你想聊什么？"},
    ]
    plan = build_presentation_plan(request("我也不知道"), history)
    assert plan["resolved"] == "dialogue"
    assert plan["question_budget"] == 0
    assert "target_characters" not in plan
    assert "minimum_content_beats" not in plan
    assert "角色自己的观点" in plan["agency_budget"]["allowed"]
    assert "共同事件与环境物件" in plan["agency_budget"]["requires_user_or_server_evidence"]
    assert "替用户补动作或身体反应" in plan["agency_budget"]["forbidden"]


def test_text_boundary_preserves_parentheses_and_model_authored_prose():
    history = [{"role": "assistant", "content": "我说完了，你怎么想？"}]
    source = "（我放下杯子。）我介意的是你什么都不说。你到底怎么想？"
    normalized = normalize_presentation_response(source, request("我不知道"), history)
    assert normalized == source


def test_scene_boundary_preserves_action_opening():
    source = "（我推开门，侧身让出位置。）进来。"
    normalized = normalize_presentation_response(source, request("我走到门口", presentation_mode="scene"), [])
    assert normalized == source


def test_roleplay_examples_are_selected_by_current_turn_category():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["examples"]["scene_transition"] = [
        "用户：我到家了 → 角色：我关掉手边的音乐，起身走向玄关。"
    ]

    layer = build_roleplay_layer(request("我到家了"), bundle, [])

    assert layer["turn_style"] == "scene_transition"
    assert layer["selected_examples"] == ["用户：我到家了 → 角色：我关掉手边的音乐，起身走向玄关。"]
    assert layer["scene"]["transition_signal"] == "我到家了"


def test_large_roleplay_details_are_loaded_only_when_the_turn_needs_them():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["appearance"] = {"hair": "黑色长直发"}
    bundle.ai_profile["roleplay"]["signature_outfit"] = {"upper": "黑色高领上衣"}
    bundle.ai_profile["roleplay"]["life_context"] = {"education": "大学医学结业"}

    ordinary = build_roleplay_layer(request("今天怎么样"), bundle, [])
    visual = build_roleplay_layer(request("你今天穿什么？"), bundle, [])
    education = build_roleplay_layer(request("你大学学的什么？"), bundle, [])

    assert "conditional_character_context" not in ordinary
    assert visual["conditional_character_context"]["appearance"]["hair"] == "黑色长直发"
    assert education["conditional_character_context"]["life_context"]["education"] == "大学医学结业"


def test_legacy_private_r18_protocol_is_not_loaded_into_the_compact_adult_packet():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["r18_protocol"] = [
        "原文规则一：直接推进。",
        "原文规则二：不要停在预告。",
    ]

    ordinary = build_roleplay_layer(request("继续"), bundle, [])
    adult = build_roleplay_layer(request("继续", adult_mode=True), bundle, [])

    assert "r18_director" not in ordinary
    assert adult["turn_style"] == "intimate"
    assert "r18_director" not in adult


def test_adult_role_correction_does_not_leak_into_daily_lane():
    history = [
        {
            "role": "assistant",
            "adult_mode": True,
            "role_quality": "drift",
            "role_quality_correction": "R18 性行为模式已开启：继续明确性行为",
        }
    ]

    layer = build_roleplay_layer(
        request("回到普通聊天", adult_mode=False),
        profiles(),
        history,
    )

    assert "previous_turn_correction" not in layer


def test_adult_mode_uses_the_intimate_turn_style_without_a_director_state():
    bundle = profiles()
    layer = build_roleplay_layer(
        request("继续", adult_mode=True, initiative=True),
        bundle,
        [{"role": "assistant", "content": "已有互动", "hidden": False}],
    )

    assert layer["turn_style"] == "intimate"
    assert "r18_director" not in layer


def test_stable_role_profile_excludes_conditional_card_payloads():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["appearance"] = {"hair": "黑色长直发"}
    bundle.ai_profile["roleplay"]["life_context"] = {"education": "大学医学结业"}
    bundle.ai_profile["roleplay"]["selfhood"]["values"] = ["忠贞"]

    compact = _role_profile(bundle.ai_profile)

    assert compact["roleplay_core"]["selfhood"]["values"] == ["忠贞"]
    assert "appearance" not in compact["roleplay_core"]
    assert "life_context" not in compact["roleplay_core"]


def test_quality_flags_repeated_unsolicited_caretaker_loop():
    history = [
        {
            "role": "assistant",
            "content": "你快去睡吧，我给你煮粥，再准备两个鸡蛋。",
        }
    ]
    result = evaluate_roleplay_quality(
        "你快去睡吧，我给你煮粥，再准备两个鸡蛋。",
        request("我刚到家"),
        history,
    )

    assert result["quality"] == "drift"
    assert "unsolicited_caretaker_loop" in result["reasons"]
    assert "repeats_recent_assistant" in result["reasons"]


def test_voice_response_discards_stage_direction_without_affecting_text_chat():
    source = "我靠近一点。（我正看着你笑）现在能听清吗？"

    voice = normalize_voice_response(
        source,
        request("继续", interaction_mode="voice"),
    )
    text = normalize_voice_response(
        source,
        request("继续", interaction_mode="text"),
    )

    assert voice == "我靠近一点。现在能听清吗？"
    assert "（" not in voice and "）" not in voice
    assert text == source


def test_hidden_voice_cue_is_removed_from_text_and_voice_delivery():
    source = "[[voice:firm]] 这件事我会处理。"
    assert normalize_voice_response(source, request("继续", interaction_mode="text")) == "这件事我会处理。"
    assert normalize_voice_response(source, request("继续", interaction_mode="voice")) == "这件事我会处理。"


def test_voice_stage_direction_is_reported_for_next_turn_correction():
    result = evaluate_roleplay_quality(
        "（笑着靠近）我听到了。",
        request("继续", interaction_mode="voice"),
        [],
    )

    assert "voice_stage_direction" in result["reasons"]
    assert "只写亲口说出的口语" in result["correction"]
    assert "第一人称动作播报" in result["correction"]


def test_r18_quality_does_not_gate_a_response_by_first_sentence_vocabulary():
    result = evaluate_roleplay_quality(
        "我吻住你，指尖慢慢解开你的衣扣，低声问你准备好了吗？",
        request("继续", adult_mode=True),
        [
            {
                "role": "assistant",
                "content": "我贴近你，手掌隔着衣料缓慢抚摸，问你想不想继续。",
            }
        ],
    )

    assert result["quality"] == "pass"
    assert result["reasons"] == []


def test_r18_quality_allows_adult_discussion_without_forcing_a_new_action():
    result = evaluate_roleplay_quality(
        "让我想想，反正不急，我有的是办法。你先说想怎么来，我再考虑。",
        request("那你想怎么弄啊？", adult_mode=True, interaction_mode="voice"),
        [{"role": "assistant", "content": "待会儿再让你好好感受。"}],
    )

    assert "r18_first_sentence_not_explicit" not in result["reasons"]


def test_r18_quality_does_not_reject_a_direct_request_response_by_vocabulary():
    result = evaluate_roleplay_quality(
        "我先抱紧你，问你想从哪里开始。",
        request("我想要", adult_mode=True),
        [],
    )

    assert result["quality"] == "pass"
    assert result["reasons"] == []


def test_r18_protocol_is_single_authoritative_text_without_legacy_ladders():
    assert "intensity_ladder" not in ADULT_ROLEPLAY_PROTOCOL
    assert "modules" not in ADULT_ROLEPLAY_PROTOCOL
    assert "直接使用鸡巴、阴茎、龟头" in ADULT_ROLEPLAY_PROTOCOL
    assert "沉浸在角色中" in ADULT_ROLEPLAY_PROTOCOL


def test_r18_quality_accepts_explicit_sexual_action_progress():
    result = evaluate_roleplay_quality(
        "我握住你的鸡巴主动进入身体，嘴里骂你这个欠操的坏东西，调整动作和节奏继续抽送。",
        request("继续", adult_mode=True),
        [],
    )

    assert result["quality"] == "pass"
    assert "r18_first_sentence_not_explicit" not in result["reasons"]


def test_r18_quality_accepts_direct_anatomical_language():
    result = evaluate_roleplay_quality(
        "我已经湿透了，手指正按在阴蒂上揉着。",
        request("我想要", adult_mode=True),
        [],
    )

    assert result["quality"] == "pass"
    assert "r18_first_sentence_not_explicit" not in result["reasons"]


def test_r18_voice_quality_does_not_reject_by_length_or_vocabulary():
    result = evaluate_roleplay_quality(
        "我现在开始动了，你待会儿可别后悔。",
        request("继续", adult_mode=True, interaction_mode="voice"),
        [],
    )

    assert result["quality"] == "pass"
    assert "r18_first_sentence_not_explicit" not in result["reasons"]
    assert "r18_missing_dirty_language" not in result["reasons"]
    assert "r18_response_too_short" not in result["reasons"]
    assert "180至250个中文字符" not in result["correction"]


def test_prompt_omits_model_write_protocol_and_loads_only_selected_roleplay_examples():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["examples"]["casual"] = [
        "用户：今天怎样 → 角色：我刚把看到一半的书扣在桌上。",
        "用户：忙吗 → 角色：我正试着解一道没解开的谜题。",
        "不应加载的第三条例子",
    ]

    prompt = build_prompt(request("今天怎样"), bundle, [], [], []).messages
    text = "\n".join(item["content"] for item in prompt)

    assert "正文与状态隔离" not in text
    assert '"trigger":"none"' not in text
    assert "用户：今天怎样 → 角色：我刚把看到一半的书扣在桌上。" not in text
    assert "不应加载的第三条例子" not in text
    assert "每个 Patch 提供" not in text
