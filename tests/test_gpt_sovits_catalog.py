from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindspace_graph.gpt_sovits import GPT_SOVITS_VOICES, voice_paths

ROOT = Path(__file__).resolve().parents[1]


def test_full_character_catalog_is_version_audited() -> None:
    document = json.loads((ROOT / "config" / "gpt-sovits-voices.json").read_text(encoding="utf-8"))
    voices = document["voices"]
    assert len(voices) == 48
    assert sum(voice["family"] == "v4" for voice in voices) == 38
    assert sum(voice["family"] == "v2ProPlus" for voice in voices) == 10
    assert all(voice["family"] == "v2ProPlus" for voice in voices if voice["franchise"] == "崩铁")
    assert all(voice["family"] == "v4" for voice in voices if voice["franchise"] != "崩铁")
    assert voices[0]["id"] == "v4-elysia-2026"
    assert all(voice["download"]["size"] > 0 for voice in voices)


def test_catalog_loader_matches_json_and_keeps_paths_in_model_root(tmp_path: Path) -> None:
    assert len(GPT_SOVITS_VOICES) == 48
    paths = voice_paths(tmp_path, "v4-elysia-2026")
    assert (
        paths["gpt_weight"]
        == (tmp_path / "tts/gpt-sovits/runtime/GPT_SoVITS/pretrained_models/s1v3.ckpt").resolve()
    )
    assert all(
        path == tmp_path.resolve() or tmp_path.resolve() in path.parents for path in paths.values()
    )


def test_catalog_path_guard_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        GPT_SOVITS_VOICES,
        "unsafe",
        {
            **GPT_SOVITS_VOICES["v4-changli"],
            "id": "unsafe",
            "directory": "../../outside",
        },
    )
    with pytest.raises(ValueError, match="越界路径"):
        voice_paths(tmp_path, "unsafe")
