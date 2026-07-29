from mindspace_graph.voice_render import (
    DEFAULT_VOICE_CUE,
    VoiceCueStream,
    extract_voice_cue,
    infer_qwen3_voice_cue,
    pace_qwen3_base_text,
    qwen3_instructions,
)


def test_voice_cue_is_removed_from_completed_spoken_text():
    cue, text, explicit = extract_voice_cue("[[voice:warm]] 早上好。")
    assert (cue, text, explicit) == ("warm", "早上好。", True)


def test_qwen3_base_pacing_slows_semantic_boundaries_without_changing_words():
    assert pace_qwen3_base_text(
        "先别急，我在听。慢慢说给我听。", speed=0.9
    ) == "先别急，……我在听。……慢慢说给我听。"
    assert pace_qwen3_base_text(
        "先别急，我在听。慢慢说给我听。", speed=1.0
    ) == "先别急，我在听。慢慢说给我听。"


def test_qwen3_base_pacing_preserves_existing_breath_and_paragraph_pause():
    assert pace_qwen3_base_text(
        "嗯……我在听。\n\n呼……慢慢说。", speed=0.9
    ) == "嗯……我在听。……呼……慢慢说。"


def test_invalid_voice_cue_falls_back_to_neutral_without_leaking_a_tag():
    cue, text, explicit = extract_voice_cue("[[voice:unknown]] 我在。")
    assert (cue, text, explicit) == (DEFAULT_VOICE_CUE, "我在。", True)


def test_adult_delivery_preset_is_only_accepted_when_the_turn_allows_it():
    cue, text, explicit = extract_voice_cue("[[voice:moaning]] 我在。", allow_adult=False)
    assert (cue, text, explicit) == (DEFAULT_VOICE_CUE, "我在。", True)
    cue, text, explicit = extract_voice_cue("[[voice:moaning]] 我在。", allow_adult=True)
    assert (cue, text, explicit) == ("moaning", "我在。", True)


def test_streamed_split_voice_cue_is_held_until_the_prefix_is_complete():
    stream = VoiceCueStream()
    assert stream.feed("[[voi") == []
    assert stream.feed("ce:playful]]你好") == ["你好"]
    assert stream.cue == "playful"
    assert stream.explicit_tag is True


def test_non_voice_text_keeps_low_latency_and_neutral_uses_natural_conversation_hint():
    stream = VoiceCueStream()
    assert stream.feed("你好") == ["你好"]
    assert qwen3_instructions("unknown") == (
        "像熟悉伴侣近距离聊天，句间自然换气，偶尔带一点很轻的笑意。"
    )


def test_style_instruction_is_short_positive_and_does_not_repeat_format_rules():
    instruction = qwen3_instructions("teasing")
    assert instruction == "像熟人闲聊，语速正常，只有很轻的调侃。"
    assert len(instruction) < 40
    assert "不要" not in instruction
    assert "CustomVoice" not in instruction


def test_alluring_is_close_and_low_energy_not_an_excited_default():
    instruction = qwen3_instructions("alluring")
    assert "低声" in instruction
    assert "重音少而轻" in instruction


def test_custom_voice_infers_only_prosody_while_requested_cue_wins():
    assert infer_qwen3_voice_cue("呵……你还真会挑时候。", "neutral") == "laughing"
    assert infer_qwen3_voice_cue("呼……让我缓一下。", "neutral") == "breathy"
    assert infer_qwen3_voice_cue("唉……算了。", "neutral") == "sighing"
    assert infer_qwen3_voice_cue("呵……别闹。", "intimate") == "intimate"


def test_custom_voice_speed_is_expressed_as_style_not_speaker_change():
    instruction = qwen3_instructions("warm", speed=0.9)
    assert instruction.startswith("语速舒缓偏慢，")
    assert "句间自然换气" in instruction
    assert "音色" not in instruction
    assert "年龄" not in instruction
