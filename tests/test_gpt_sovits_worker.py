from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mindspace_gpt_sovits_worker",
    ROOT / "vendor" / "gpt_sovits_mindspace_worker.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_v4_voice_prosody_uses_official_punctuation_controls(tmp_path: Path) -> None:
    worker = MODULE.GPTSoVITSWorker.__new__(MODULE.GPTSoVITSWorker)
    worker.voice = {
        "family": "v4",
        "reference_text": "参考文本。",
        "reference_language": "zh",
        "prosody": {
            "top_k": 20,
            "top_p": 0.6,
            "temperature": 0.6,
            "text_split_method": "cut5",
            "fragment_interval": 0.24,
        },
    }
    worker._voice_paths = lambda _voice: {"reference": tmp_path / "reference.wav"}

    inputs = worker._inputs("你好，欢迎回来。", 1.0)

    assert inputs["top_k"] == 20
    assert inputs["top_p"] == 0.6
    assert inputs["temperature"] == 0.6
    assert inputs["text_split_method"] == "cut5"
    assert inputs["fragment_interval"] == 0.24
    assert inputs["return_fragment"] is True
    assert inputs["streaming_mode"] is False


def test_boundary_silence_trim_preserves_cadence_not_long_vocoder_gaps() -> None:
    worker = MODULE.GPTSoVITSWorker.__new__(MODULE.GPTSoVITSWorker)
    worker.np = np
    worker.sample_rate = 48_000
    leading_noise = np.full(48_000 * 2, 20, dtype=np.int16)
    speech = np.full(48_000, 2_000, dtype=np.int16)
    trailing_noise = np.full(48_000 * 5, 20, dtype=np.int16)

    trimmed = worker._trim_boundary_silence(
        np.concatenate([leading_noise, speech, trailing_noise]).tobytes()
    )
    samples = np.frombuffer(trimmed, dtype=np.int16)

    assert 48_000 * 1.20 <= samples.size <= 48_000 * 1.30
    assert np.max(np.abs(samples)) == 2_000


def test_voice_switch_reloads_catalog_for_newly_installed_voice(tmp_path: Path) -> None:
    catalog = tmp_path / "voices.json"
    catalog.write_text(
        json.dumps({"voices": [{"id": "v4-new", "label": "新音色", "family": "v4"}]}),
        encoding="utf-8",
    )
    worker = MODULE.GPTSoVITSWorker.__new__(MODULE.GPTSoVITSWorker)
    worker.catalog_path = catalog
    worker.voices = {"v4-old": {"id": "v4-old", "label": "旧音色", "family": "v4"}}
    worker.voice_id = "v4-old"
    worker._warmup = lambda _text: None

    def load_voice(voice_id: str) -> None:
        assert voice_id in worker.voices
        worker.voice_id = voice_id
        worker.voice = worker.voices[voice_id]
        worker.sample_rate = 48_000

    worker._load_voice = load_voice

    result = worker.select_voice("v4-new")

    assert result["ok"] is True
    assert result["voice_id"] == "v4-new"
    assert "v4-new" in worker.voices
