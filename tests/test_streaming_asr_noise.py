from __future__ import annotations

import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np

from mindspace_graph.streaming_asr import (
    ASRSessionOptions,
    ASRTextCorrector,
    FunASRRuntime,
    FunASRStreamSession,
    GPUInferenceScheduler,
    apply_asr_decision,
    apply_final_refinement,
)


def test_low_confidence_playback_text_remains_draft_only() -> None:
    event = apply_asr_decision(
        {
            "event": "asr.final",
            "data": {
                "text": "像是一个没听清的人名",
                "playback_active": True,
                "vad_confirmed": True,
                "stable_partial": False,
            },
        }
    )

    assert event["data"]["quality"] == "uncertain"
    assert event["data"]["confirmed_text"] == ""
    assert event["data"]["barge_in_eligible"] is False
    assert event["data"]["uncertain_segments"] == [
        {"text": "像是一个没听清的人名", "reason": "playback_unstable_text"}
    ]


def test_tts_echo_is_rejected_even_when_vad_detects_a_voice() -> None:
    event = apply_asr_decision(
        {
            "event": "asr.final",
            "data": {
                "text": "今天我们继续聊这个话题",
                "playback_text": "好的，今天我们继续聊这个话题。",
                "playback_active": True,
                "vad_confirmed": True,
                "stable_partial": True,
            },
        }
    )

    assert event["data"]["quality"] == "rejected"
    assert event["data"]["confirmed_text"] == ""
    assert event["data"]["barge_in_eligible"] is False
    assert "playback_echo" in event["data"]["decision_reasons"]


def test_explicit_stop_is_fast_barge_in_but_still_requires_vad() -> None:
    accepted = apply_asr_decision(
        {
            "event": "asr.final",
            "data": {
                "text": "等一下",
                "playback_active": True,
                "vad_confirmed": True,
            },
        }
    )
    rejected = apply_asr_decision(
        {
            "event": "asr.final",
            "data": {
                "text": "等一下",
                "playback_active": True,
                "vad_confirmed": False,
            },
        }
    )

    assert accepted["data"]["quality"] == "accepted"
    assert accepted["data"]["barge_in_eligible"] is True
    assert accepted["data"]["explicit_stop"] is True
    assert rejected["data"]["quality"] == "rejected"
    assert rejected["data"]["barge_in_eligible"] is False


def test_uncommon_name_disagreement_keeps_only_the_reliable_backbone() -> None:
    event = apply_asr_decision(
        {
            "event": "asr.final",
            "data": {
                "text": "我想找阿斯塔利昂帮我配音",
                "stream_text": "我想找长离帮我配音",
                "refinement": {"applied": True},
                "playback_active": False,
                "vad_confirmed": True,
            },
        }
    )

    assert event["data"]["quality"] == "uncertain"
    assert event["data"]["confirmed_text"] == "我想找帮我配音"
    assert event["data"]["uncertain_segments"] == [
        {"text": "阿斯塔利昂", "reason": "stream_final_disagreement"}
    ]


def test_runtime_waits_for_an_in_progress_preload(monkeypatch, tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class SlowAutoModel:
        def __init__(self, *, model: str, **_: object) -> None:
            calls.append(model)
            if len(calls) == 1:
                started.set()
                assert release.wait(timeout=2)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=SlowAutoModel))
    runtime = FunASRRuntime(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        preload = executor.submit(runtime.load)
        assert started.wait(timeout=1)
        connection = executor.submit(runtime.load)
        time.sleep(0.03)
        assert not connection.done()
        release.set()
        assert preload.result(timeout=2) is True
        assert connection.result(timeout=2) is True
    assert len(calls) == 3


def test_runtime_serializes_shared_model_inference(tmp_path) -> None:
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    class SharedModel:
        def generate(self, **_: object) -> list[object]:
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return []

    runtime = FunASRRuntime(tmp_path)
    runtime.asr = SharedModel()
    sessions = [
        FunASRStreamSession(runtime, ASRSessionOptions(energy_threshold=0))
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(session.feed, _pcm(0.05, 480)) for session in sessions]
        for future in futures:
            future.result(timeout=2)

    assert maximum_active == 1


def test_gpu_scheduler_gives_waiting_stream_priority_over_final_pass() -> None:
    scheduler = GPUInferenceScheduler()
    order: list[str] = []
    release = threading.Event()

    def enter(priority: str) -> None:
        with scheduler.slot(priority, timeout=1) as acquired:
            assert acquired is True
            order.append(priority)

    with scheduler.slot("stream"):
        final_thread = threading.Thread(target=enter, args=("final",))
        final_thread.start()
        while scheduler.status()["waiting_finals"] != 1:
            time.sleep(0.005)
        stream_thread = threading.Thread(target=enter, args=("stream",))
        stream_thread.start()
        while scheduler.status()["waiting_streams"] != 1:
            time.sleep(0.005)
        release.set()
    stream_thread.join(timeout=1)
    final_thread.join(timeout=1)

    assert order == ["stream", "final"]


class _SilentModel:
    def generate(self, **_: object) -> list[object]:
        return []


class _Runtime:
    error = ""
    vad = None
    punc = None
    asr = _SilentModel()

    @staticmethod
    def load() -> bool:
        return True


class _SpeechVad:
    def generate(self, **_: object) -> list[dict[str, list[list[int]]]]:
        return [{"value": [[0, -1]]}]


class _NoSpeechVad:
    def generate(self, **_: object) -> list[object]:
        return []


class _RuntimeWithVad(_Runtime):
    def __init__(self, vad: object) -> None:
        self.vad = vad


class _TextModel:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate(self, **_: object) -> list[dict[str, str]]:
        return [{"text": self.text}]


class _RuntimeWithText(_RuntimeWithVad):
    def __init__(self, vad: object, text: str) -> None:
        super().__init__(vad)
        self.asr = _TextModel(text)


def _pcm(amplitude: float, duration_ms: int, sample_rate: int = 16000) -> bytes:
    count = int(sample_rate * duration_ms / 1000)
    samples = np.full(count, amplitude, dtype="float32")
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def _breath_pcm(amplitude: float, duration_ms: int, sample_rate: int = 16000) -> bytes:
    count = int(sample_rate * duration_ms / 1000)
    samples = np.random.default_rng(42).normal(0, amplitude, count).astype("float32")
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def test_breath_like_noise_below_threshold_does_not_start_speech() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-35 / 20),
            min_speech_ms=120,
        ),
    )

    events = []
    for _ in range(10):
        events.extend(session.feed(_pcm(0.012, 40)))
    events.extend(session.feed(_pcm(0.0, 40)))

    assert not any(event["event"] == "asr.speech_start" for event in events)
    assert session.speaking is False


def test_short_loud_transient_does_not_start_speech() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-35 / 20),
            min_speech_ms=120,
        ),
    )

    events = session.feed(_pcm(0.05, 80))
    events.extend(session.feed(_pcm(0.0, 40)))

    assert not any(event["event"] == "asr.speech_start" for event in events)


def test_sustained_voice_above_threshold_starts_once() -> None:
    threshold = 10 ** (-35 / 20)
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(energy_threshold=threshold, min_speech_ms=120),
    )

    events = []
    for _ in range(3):
        events.extend(session.feed(_pcm(0.05, 40)))

    starts = [event for event in events if event["event"] == "asr.speech_start"]
    assert len(starts) == 1
    assert starts[0]["data"]["energy_db"] > -35
    assert math.isclose(session.voiced_ms, 120, abs_tol=1)


def test_playback_profile_reports_candidate_without_interrupting_short_sound() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            candidate_release_ms=160,
            playback_active=True,
        ),
    )

    events = session.feed(_pcm(0.05, 80))
    for _ in range(4):
        events.extend(session.feed(_pcm(0.0, 40)))

    assert any(event["event"] == "asr.speech_candidate" for event in events)
    assert any(event["event"] == "asr.speech_candidate_cleared" for event in events)
    assert not any(event["event"] == "asr.speech_start" for event in events)


def test_same_playback_state_threshold_update_preserves_active_candidate() -> None:
    session = FunASRStreamSession(
        _RuntimeWithVad(_NoSpeechVad()),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )

    session.feed(_pcm(0.05, 160))
    assert session.candidate_active is True
    assert session.voiced_ms == 160

    changed = session.configure_playback(
        playing=True,
        energy_threshold=10 ** (-31 / 20),
        min_speech_ms=300,
        candidate_release_ms=240,
    )

    assert changed is False
    assert session.candidate_active is True
    assert session.voiced_ms == 160


def test_real_playback_transition_resets_unconfirmed_candidate() -> None:
    session = FunASRStreamSession(
        _RuntimeWithVad(_NoSpeechVad()),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )
    session.feed(_pcm(0.05, 160))

    changed = session.configure_playback(
        playing=False,
        energy_threshold=10 ** (-36 / 20),
        min_speech_ms=160,
        candidate_release_ms=240,
    )

    assert changed is True
    assert session.candidate_active is False
    assert session.pending_speech_start is False
    assert session.voiced_ms == 0


def test_listening_profile_accepts_an_eighty_millisecond_short_reply() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-38 / 20),
            min_speech_ms=80,
            playback_active=False,
        ),
    )

    events = session.feed(_pcm(0.03, 80))

    starts = [event for event in events if event["event"] == "asr.speech_start"]
    assert len(starts) == 1
    assert starts[0]["data"]["playback_active"] is False


def test_playback_profile_does_not_interrupt_on_energy_without_vad_speech() -> None:
    session = FunASRStreamSession(
        _RuntimeWithVad(_NoSpeechVad()),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )

    events = session.feed(_pcm(0.05, 480))

    assert any(event["event"] == "asr.speech_candidate" for event in events)
    assert not any(event["event"] == "asr.speech_start" for event in events)
    assert session.pending_speech_start is True
    assert session.speaking is False


def test_playback_profile_confirms_barge_in_with_vad() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "我在说话"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )

    events = session.feed(_pcm(0.05, 480))

    starts = [event for event in events if event["event"] == "asr.speech_start"]
    assert len(starts) == 1
    assert starts[0]["data"]["confirmed_by"] == "fsmn_vad+asr"
    assert session.speaking is True


def test_playback_profile_confirms_barge_in_before_asr_has_text() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), ""),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )

    events = session.feed(_pcm(0.05, 480))

    starts = [event for event in events if event["event"] == "asr.speech_start"]
    assert len(starts) == 1
    assert starts[0]["data"]["confirmed_by"] == "fsmn_vad"
    assert session.speaking is True


def test_input_gate_drops_pcm_and_clears_unfinished_candidate() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "锁定期间不应识别"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=300,
            playback_active=True,
        ),
    )
    session.feed(_pcm(0.05, 160))
    assert session.candidate_active is True

    assert session.configure_input_gate(True) is True
    assert session.candidate_active is False
    assert session.feed(_pcm(0.05, 480)) == []
    assert session.speaking is False

    assert session.configure_input_gate(False) is True
    events = session.feed(_pcm(0.05, 480))
    assert any(event["event"] == "asr.speech_start" for event in events)


def test_playback_candidate_with_text_is_deferred_instead_of_interrupting() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_NoSpeechVad(), "等一下"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-30 / 20),
            min_speech_ms=420,
            candidate_release_ms=280,
            playback_active=True,
            deferred_during_playback=True,
        ),
    )

    events = session.feed(_pcm(0.05, 480))
    events.extend(session.feed(_pcm(0.0, 480)))

    assert not any(event["event"] == "asr.speech_start" for event in events)
    deferred = [event for event in events if event["event"] == "asr.deferred"]
    assert len(deferred) == 1
    assert "等一下" in deferred[0]["data"]["text"]


def test_final_text_includes_raw_text_and_deterministic_correction_metadata() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "我想听长利说话"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=0.01,
            min_speech_ms=80,
            explicit_corrections={"长利": "长离"},
            vocabulary_revision="v-test",
        ),
    )

    events = session.feed(_pcm(0.05, 480), force_final=True)
    final = next(event for event in events if event["event"] == "asr.final")

    assert final["data"]["raw_text"] == "我想听长利说话"
    assert final["data"]["text"] == "我想听长离说话"
    assert final["data"]["vocabulary_revision"] == "v-test"
    assert final["data"]["correction_matches"][0]["to"] == "长离"
    finalized_pcm, playback_active = session.pop_finalized_audio()
    assert len(finalized_pcm) >= len(_pcm(0.05, 480))
    assert playback_active is False
    assert session.pop_finalized_audio() == (b"", False)


def test_stream_session_does_not_allocate_a_dormant_emotion_buffer() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(energy_threshold=0, min_speech_ms=80),
    )

    session.feed(_pcm(0.05, 2400))
    assert not hasattr(session, "_emotion_pcm")


def test_silence_final_does_not_wait_for_a_full_asr_chunk() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "说完了"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=0.01,
            min_speech_ms=80,
            silence_ms=250,
        ),
    )

    session.feed(_pcm(0.05, 480))
    events: list[dict[str, object]] = []
    for _ in range(3):
        events.extend(session.feed(_pcm(0.0, 100)))

    assert any(event["event"] == "asr.final" for event in events)


def test_weak_breath_during_tail_does_not_reset_fast_endpoint() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-36 / 20),
            min_speech_ms=120,
            silence_ms=250,
            dynamic_endpointing=False,
        ),
    )

    session.feed(_pcm(0.05, 120))
    events = session.feed(_pcm(0.0, 100))
    events.extend(session.feed(_breath_pcm(0.02, 100)))
    events.extend(session.feed(_pcm(0.0, 100)))

    assert any(event["event"] == "asr.final" for event in events)
    assert session.speaking is False


def test_clear_resumed_speech_cancels_tail_endpoint_after_confirmation() -> None:
    session = FunASRStreamSession(
        _Runtime(),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-36 / 20),
            min_speech_ms=120,
            silence_ms=250,
            tail_resume_min_ms=100,
        ),
    )

    session.feed(_pcm(0.05, 120))
    session.feed(_pcm(0.0, 100))
    events = session.feed(_pcm(0.05, 120))
    events.extend(session.feed(_pcm(0.0, 200)))

    assert not any(event["event"] == "asr.final" for event in events)
    assert session.speaking is True
    assert session.silence_ms == 200


def test_dynamic_endpoint_waits_longer_after_chinese_hesitation() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "然后"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=0.01,
            min_speech_ms=80,
            silence_ms=600,
        ),
    )

    session.feed(_pcm(0.05, 480))
    events = session.feed(_pcm(0.0, 600))
    assert not any(event["event"] == "asr.final" for event in events)
    events.extend(session.feed(_pcm(0.0, 300)))

    final = next(event for event in events if event["event"] == "asr.final")
    assert final["data"]["endpoint_reason"] == "hesitation_tail"
    assert final["data"]["endpoint_silence_ms"] == 900


def test_dynamic_endpoint_finishes_early_after_terminal_punctuation() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_SpeechVad(), "说完了。"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=0.01,
            min_speech_ms=80,
            silence_ms=600,
        ),
    )

    session.feed(_pcm(0.05, 480))
    events = session.feed(_pcm(0.0, 400))

    final = next(event for event in events if event["event"] == "asr.final")
    assert final["data"]["endpoint_reason"] == "sentence_terminal"
    assert final["data"]["endpoint_silence_ms"] == 400


def test_final_refinement_uses_bounded_chinese_single_batch_and_hotwords(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    class FinalModel:
        def generate(self, **kwargs: object) -> list[dict[str, str]]:
            calls.append(kwargs)
            return [{"text": "我想听长利说话。"}]

    runtime = FunASRRuntime(tmp_path)
    runtime.final_asr = FinalModel()
    options = ASRSessionOptions(decoder_hotwords=("长离", "Mindspace"))

    result = runtime.refine_final_pcm(
        _pcm(0.05, 800),
        options,
        playback_active=False,
    )

    assert result["applied"] is True
    assert result["text"] == "我想听长利说话。"
    assert calls[0]["batch_size"] == 1
    assert calls[0]["language"] == "中文"
    assert calls[0]["max_length"] == 192
    assert calls[0]["hotwords"] == ["长离", "Mindspace"]


def test_final_refinement_preserves_stream_text_and_reapplies_corrections() -> None:
    options = ASRSessionOptions(explicit_corrections={"长利": "长离"})
    event = {
        "event": "asr.final",
        "data": {"text": "旧结果", "raw_text": "旧结果", "correction_matches": []},
    }

    apply_final_refinement(
        event,
        {"applied": True, "reason": "ok", "text": "我想听长利说话"},
        ASRTextCorrector(options),
    )

    assert event["data"]["stream_text"] == "旧结果"
    assert event["data"]["raw_text"] == "我想听长利说话"
    assert event["data"]["text"] == "我想听长离说话"
    assert event["data"]["refinement"]["reason"] == "ok"


def test_final_refinement_skips_playback_audio_to_avoid_tts_echo(tmp_path) -> None:
    runtime = FunASRRuntime(tmp_path)
    runtime.final_asr = _TextModel("不应调用")

    result = runtime.refine_final_pcm(
        _pcm(0.05, 800),
        ASRSessionOptions(),
        playback_active=True,
    )

    assert result["applied"] is False
    assert result["reason"] == "playback_echo_risk"


def test_normal_listening_does_not_accept_asr_hallucination_without_vad() -> None:
    session = FunASRStreamSession(
        _RuntimeWithText(_NoSpeechVad(), "嗯"),  # type: ignore[arg-type]
        ASRSessionOptions(
            energy_threshold=10 ** (-36 / 20),
            min_speech_ms=120,
            candidate_release_ms=250,
        ),
    )

    events = session.feed(_breath_pcm(0.02, 480))
    events.extend(session.feed(_pcm(0.0, 480)))

    assert not any(event["event"] == "asr.speech_start" for event in events)
    assert not any(event["event"] == "asr.partial" for event in events)
    assert session.transcript == ""
    assert session.speaking is False
