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
from mindspace_graph.roleplay import (
    allow_raw_chat_retrieval,
    build_roleplay_layer,
    evaluate_roleplay_quality,
    normalize_voice_response,
)
from mindspace_graph.r18_director import build_style_packet


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


def test_profile_v12_migrates_missing_roleplay_sections_without_overwriting(tmp_path):
    repository = JsonProfileRepository(tmp_path / "profiles")
    bundle = repository.load_bundle()

    assert bundle.ai_profile["schema_version"] == "1.2.0"
    assert "roleplay" in bundle.ai_profile
    assert "roleplay_state" in bundle.runtime_state


def test_profile_v11_migration_preserves_existing_character_content(tmp_path):
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

    assert document["schema_version"] == "1.2.0"
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


def test_private_r18_protocol_is_loaded_only_for_explicit_r18_mode():
    bundle = profiles()
    bundle.ai_profile["roleplay"]["r18_protocol"] = [
        "原文规则一：直接推进。",
        "原文规则二：不要停在预告。",
    ]

    ordinary = build_roleplay_layer(request("继续"), bundle, [])
    adult = build_roleplay_layer(request("继续", adult_mode=True), bundle, [])

    assert "r18_director" not in ordinary
    assert adult["turn_style"] == "intimate"
    assert adult["r18_director"]["private_overlay"][-1] == "原文规则一：直接推进。"
    assert adult["r18_director"]["library_sources"]["character_overlay"] is True


def test_r18_director_does_not_upgrade_on_initiative_silence():
    bundle = profiles()
    layer = build_roleplay_layer(
        request("继续", adult_mode=True, initiative=True),
        bundle,
        [{"role": "assistant", "content": "已有互动", "hidden": False}],
    )

    assert layer["r18_director"]["scene_state"]["engagement"] == "silent"
    assert layer["r18_director"]["scene_state"]["advance"] is False


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


def test_r18_quality_marks_foreplay_only_response_as_hard_drift():
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

    assert result["quality"] == "drift"
    assert "r18_missing_sexual_action" in result["reasons"]
    assert "r18_foreplay_loop" in result["reasons"]
    assert "不能再以亲吻、抚摸、挑逗、询问、威胁或预告代替推进" in result["correction"]


def test_r18_quality_treats_delayed_promises_as_foreplay_loop():
    result = evaluate_roleplay_quality(
        "让我想想，反正不急，我有的是办法。你先说想怎么来，我再考虑。",
        request("那你想怎么弄啊？", adult_mode=True, interaction_mode="voice"),
        [{"role": "assistant", "content": "待会儿再让你好好感受。"}],
    )

    assert result["quality"] == "drift"
    assert "r18_missing_sexual_action" in result["reasons"]
    assert "r18_foreplay_loop" in result["reasons"]


def test_r18_intensity_ladder_skips_repeated_delay_and_requires_actual_sex():
    history = [
        {"role": "assistant", "content": "让我想想，待会儿再说。"},
        {"role": "user", "content": "好"},
        {"role": "assistant", "content": "你先说想怎么来，我再考虑。"},
    ]

    packet = build_style_packet(
        request("那你想怎么弄啊？", adult_mode=True, interaction_mode="voice"),
        history,
    )

    minimum = packet["intensity_ladder"]["current_minimum"]
    assert minimum["level"] == 4
    assert minimum["label"] == "淫语与实质行为"
    assert minimum["must_advance_beyond_previous"] is True
    assert "正文不得低于 current_minimum" in packet["intensity_ladder"]["rule"]


def test_r18_quality_accepts_explicit_sexual_action_progress():
    result = evaluate_roleplay_quality(
        "我握住你的鸡巴主动进入身体，嘴里骂你这个欠操的坏东西，调整动作和节奏继续抽送。",
        request("继续", adult_mode=True),
        [],
    )

    assert result["quality"] == "pass"
    assert "r18_missing_sexual_action" not in result["reasons"]


def test_r18_voice_quality_rejects_short_clean_euphemisms():
    result = evaluate_roleplay_quality(
        "我现在开始动了，你待会儿可别后悔。",
        request("继续", adult_mode=True, interaction_mode="voice"),
        [],
    )

    assert result["quality"] == "drift"
    assert "r18_missing_sexual_action" in result["reasons"]
    assert "r18_missing_dirty_language" in result["reasons"]
    assert "r18_response_too_short" in result["reasons"]
    assert "180至250个中文字符" in result["correction"]


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
    assert "不应加载的第三条例子" in text
    assert "每个 Patch 提供" not in text
