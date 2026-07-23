"""Deterministic GPT-SoVITS voice catalog and installed-file checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_PATH = _PROJECT_ROOT / "config" / "gpt-sovits-voices.json"


def _load_catalog() -> dict[str, dict[str, Any]]:
    document = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    voices = document.get("voices")
    if not isinstance(voices, list) or not voices:
        raise RuntimeError("GPT-SoVITS 音色目录为空")
    result: dict[str, dict[str, Any]] = {}
    required = {
        "id", "label", "character", "franchise", "family", "sample_rate",
        "component_id", "directory", "gpt_weight", "sovits_weight",
        "reference_audio", "reference_text", "reference_language",
    }
    for voice in voices:
        if not isinstance(voice, dict) or required - voice.keys():
            raise RuntimeError("GPT-SoVITS 音色目录包含不完整条目")
        voice_id = str(voice["id"])
        if voice_id in result:
            raise RuntimeError(f"GPT-SoVITS 音色 ID 重复：{voice_id}")
        result[voice_id] = voice
    return result


GPT_SOVITS_VOICES = _load_catalog()


def voice_definition(voice_id: str) -> dict[str, Any]:
    try:
        return GPT_SOVITS_VOICES[voice_id]
    except KeyError as exc:
        raise ValueError(f"未知 GPT-SoVITS 音色：{voice_id}") from exc


def _safe_model_path(model_root: Path, path: Path) -> Path:
    base = model_root.resolve()
    resolved = path.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError("GPT-SoVITS 音色目录包含越界路径")
    return resolved


def voice_paths(model_root: Path, voice_id: str) -> dict[str, Path]:
    voice = voice_definition(voice_id)
    root = _safe_model_path(model_root, model_root / str(voice["directory"]))
    return {
        "root": root,
        "gpt_weight": _safe_model_path(model_root, root / str(voice["gpt_weight"])),
        "sovits_weight": _safe_model_path(model_root, root / str(voice["sovits_weight"])),
        "reference_audio": _safe_model_path(model_root, root / str(voice["reference_audio"])),
    }


def voice_is_installed(model_root: Path, voice_id: str) -> bool:
    paths = voice_paths(model_root, voice_id)
    return all(
        path.is_file() and path.stat().st_size > 0 for key, path in paths.items() if key != "root"
    )


def public_voice_catalog(model_root: Path, active_voice: str) -> dict[str, Any]:
    items = []
    for voice_id, voice in GPT_SOVITS_VOICES.items():
        items.append(
            {
                key: value
                for key, value in voice.items()
                if key
                not in {
                    "directory", "gpt_weight", "sovits_weight", "reference_audio",
                    "prosody", "download",
                }
            }
            | {
                "installed": voice_is_installed(model_root, voice_id),
                "selected": voice_id == active_voice,
            }
        )
    return {"provider": "gpt-sovits", "active_voice": active_voice, "items": items}
