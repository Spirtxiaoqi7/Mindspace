"""Runtime-editable product configuration with redacted public snapshots."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

from mindspace_graph.adapters.file_storage import _atomic_json
from mindspace_graph.capabilities import DEFAULT_CAPABILITY_SETTINGS
from mindspace_graph.gpt_sovits import GPT_SOVITS_VOICES
from mindspace_graph.settings import AppSettings


def _merge_known(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if key not in base:
            continue
        if isinstance(base[key], dict) and isinstance(value, dict):
            merged[key] = _merge_known(base[key], value)
        else:
            merged[key] = value
    return merged


class ProductConfigStore:
    def __init__(self, path: Path, settings: AppSettings) -> None:
        self.path = path
        self.settings = settings
        self._lock = RLock()
        self._config = self._defaults()
        if path.exists():
            loaded: dict[str, Any] | None = None
            try:
                with path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                loaded = raw if isinstance(raw, dict) else None
                if isinstance(loaded, dict):
                    self._config = _merge_known(self._config, loaded)
            except (OSError, json.JSONDecodeError):
                pass
            # Demo mode was exposed by older product builds and produced a
            # deterministic placeholder while still displaying the configured
            # model name. It remains available to isolated tests, but persisted
            # user configurations always migrate to the real provider.
            if self._config["llm"].get("mode") == "demo":
                self._config["llm"]["mode"] = "openai"
            if self._config["appearance"].get("theme") == "system":
                self._config["appearance"]["theme"] = "mindscape"
            loaded_audio = loaded.get("audio", {}) if isinstance(loaded, dict) else {}
            if "tts_qwen3_vllm_task_type" not in loaded_audio:
                # Migrate only configurations written before the task type
                # existed. Later explicit voice choices remain user-owned.
                self._config["audio"]["tts_qwen3_vllm_task_type"] = "CustomVoice"
                self._config["audio"]["tts_qwen3_vllm_voice"] = "serena"
            elif (
                str(self._config["audio"].get("tts_qwen3_vllm_task_type")).lower() == "base"
                and self._config["audio"].get("tts_qwen3_vllm_voice") == "mindspace_mature_alluring"
            ):
                # 0.5.41/0.5.42 briefly shipped the Base clone profile. It
                # stabilised timbre but removed CustomVoice's actual prosody
                # controls. Restore the expressive engine once, while leaving
                # every unrelated user setting intact.
                self._config["audio"]["tts_qwen3_vllm_task_type"] = "CustomVoice"
                self._config["audio"]["tts_qwen3_vllm_voice"] = "serena"
            loaded_schema_version = str(self._config.get("schema_version") or "")
            if loaded_schema_version == "1.0.0":
                # 250 ms was the old fixed endpoint and truncated hesitant Chinese.
                # Migrate only that exact legacy default; explicit custom values stay intact.
                if self._config["audio"].get("asr_silence_ms") == 250:
                    self._config["audio"]["asr_silence_ms"] = 600
            if loaded_schema_version in {"1.0.0", "1.1.0"}:
                # 1.2 adds a user-owned, persistent voice entry choice and scene.
                # Missing values were already filled by _merge_known above.
                self._config["schema_version"] = "1.2.0"
            # 350 ms (and the frontend's former 160 ms punctuation shortcut)
            # committed natural Chinese pauses too early. Migrate only the exact
            # shipped default; longer user-selected windows remain untouched.
            if self._config["audio"].get("asr_utterance_merge_ms") == 350:
                self._config["audio"]["asr_utterance_merge_ms"] = 1100
            # Earlier releases shipped listening gates that were too strict for
            # quiet Mandarin. Migrate only the exact former defaults so a user's
            # deliberately tuned values are not overwritten.
            if self._config["audio"].get("asr_listening_energy_threshold_db") in {
                -45,
                -36,
            }:
                self._config["audio"]["asr_listening_energy_threshold_db"] = -50.0
            if self._config["audio"].get("asr_listening_min_speech_ms") == 160:
                self._config["audio"]["asr_listening_min_speech_ms"] = 120
            if self._config["audio"].get("asr_barge_in_energy_threshold_db") in {
                -30,
                -27,
            }:
                self._config["audio"]["asr_barge_in_energy_threshold_db"] = -38.0
            if self._config["audio"].get("asr_energy_threshold_db") == -35:
                self._config["audio"]["asr_energy_threshold_db"] = -50.0
            if self._config["audio"].get("asr_noise_gate_db") in {-45, -42}:
                self._config["audio"]["asr_noise_gate_db"] = -55.0
            self._config["audio"]["asr_adaptive_noise_enabled"] = False
            if loaded != self._config:
                _atomic_json(path, self._config)
        else:
            _atomic_json(path, self._config)
        self._apply_live_settings()

    def _defaults(self) -> dict[str, Any]:
        return {
            "schema_version": "1.2.0",
            "llm": {
                "mode": self.settings.llm_mode,
                "base_url": self.settings.llm_base_url,
                "api_key": self.settings.llm_api_key,
                "model": self.settings.llm_model,
                "temperature": 0.7,
                "max_tokens": 2000,
                "context_window": self.settings.llm_context_window,
                "compaction_enabled": self.settings.context_compaction_enabled,
                "compaction_model": self.settings.context_compaction_model,
                "compaction_max_tokens": self.settings.context_compaction_max_tokens,
                "compaction_soft_ratio": self.settings.context_compaction_soft_ratio,
                "compaction_hard_ratio": self.settings.context_compaction_hard_ratio,
                "compaction_patch_limit": self.settings.context_compaction_patch_limit,
                "compaction_retain_turns": self.settings.context_compaction_retain_turns,
                "compaction_delay_seconds": self.settings.context_compaction_delay_seconds,
                "role_audit_enabled": self.settings.role_audit_enabled,
                "role_audit_model": self.settings.role_audit_model,
            },
            "persona": {
                "user_name": "用户",
                "user_persona": "",
                "character_name": "Mindspace",
                "system_prompt": "",
                "reply_length_preference": "",
            },
            "retrieval": {
                "rag_enabled": True,
                "knowledge_enabled": True,
                "chat_enabled": True,
                "structured_memory_enabled": True,
                "temporal_enabled": True,
                "bm25_enabled": True,
                "vector_enabled": True,
                "knowledge_k": 2,
                "chat_k": 3,
                "history_k": 3,
                "similarity_threshold": 0.5,
                "decay_rounds": 20,
                "decay_hours": 168,
                "fairness_enabled": True,
                "low_exposure_ratio": 0.2,
                "memory_family_limit": 2,
                "starvation_rounds": 6,
                "starvation_boost": 0.12,
                "rrf_k": 60,
                "candidate_multiplier": 4,
                "max_total_boost": 0.25,
                "reranker_enabled": False,
                "reranker_top_n": 12,
                "role_query_prefix": True,
                "boosts": {
                    "knowledge_user": 0.08,
                    "knowledge_character": 0.08,
                    "knowledge_source": 0.05,
                    "chat_session": 0.15,
                    "chat_exact": 0.1,
                    "chat_text": 0.04,
                },
            },
            "knowledge": {"child_size": 700, "parent_size": 1400, "overlap": 100},
            "protocol": {
                "mode": "strict",
                "auto_repair": True,
                "diagnostics": True,
            },
            "audio": {
                "tts_provider": self.settings.tts_provider,
                "tts_worker_url": self.settings.tts_worker_url,
                "tts_reference_audio": self.settings.tts_reference_audio,
                "tts_reference_text": self.settings.tts_reference_text,
                "tts_siliconflow_base_url": self.settings.tts_siliconflow_base_url,
                "tts_siliconflow_api_key": self.settings.tts_siliconflow_api_key,
                "tts_siliconflow_model": self.settings.tts_siliconflow_model,
                "tts_siliconflow_voice": self.settings.tts_siliconflow_voice,
                "tts_siliconflow_gain": self.settings.tts_siliconflow_gain,
                "tts_siliconflow_sample_rate": self.settings.tts_siliconflow_sample_rate,
                "tts_gpt_sovits_worker_url": self.settings.tts_gpt_sovits_worker_url,
                "tts_gpt_sovits_voice": self.settings.tts_gpt_sovits_voice,
                "tts_qwen3_vllm_url": self.settings.tts_qwen3_vllm_url,
                "tts_qwen3_vllm_model": self.settings.tts_qwen3_vllm_model,
                "tts_qwen3_vllm_voice": self.settings.tts_qwen3_vllm_voice,
                "tts_qwen3_vllm_task_type": self.settings.tts_qwen3_vllm_task_type,
                "tts_qwen3_vllm_language": self.settings.tts_qwen3_vllm_language,
                "tts_speed": 1.0,
                "auto_tts": self.settings.auto_tts,
                "asr_provider": self.settings.asr_provider,
                "asr_base_url": self.settings.asr_base_url,
                "asr_api_key": self.settings.asr_api_key,
                "asr_model": self.settings.asr_model,
                "asr_auto_send": True,
                "asr_silence_ms": 600,
                "asr_energy_threshold_db": -50.0,
                "asr_noise_gate_db": -55.0,
                "asr_min_speech_ms": 120,
                # Quiet or unclear Mandarin reaches FunASR VAD instead of being
                # discarded by the inexpensive pre-VAD energy candidate gate.
                "asr_listening_energy_threshold_db": -50.0,
                "asr_listening_min_speech_ms": 120,
                # Playback remains stricter than ordinary listening. The API
                # applies a further -4 dB candidate allowance before VAD and
                # semantic echo arbitration decide whether to interrupt TTS.
                "asr_barge_in_energy_threshold_db": -38.0,
                "asr_barge_in_min_speech_ms": 300,
                "asr_candidate_release_ms": 280,
                "asr_barge_in_cooldown_ms": 1500,
                "asr_false_candidate_backoff_ms": 3000,
                "asr_duplicate_text_window_ms": 3000,
                # Retained for settings-file compatibility. Runtime arbitration
                # now uses stable gates plus VAD instead of a startup calibration.
                "asr_adaptive_noise_enabled": False,
                "asr_noise_calibration_ms": 1500,
                "asr_listening_noise_margin_db": 10.0,
                "asr_barge_in_noise_margin_db": 16.0,
                "asr_utterance_merge_ms": 1100,
                "asr_deferred_during_playback": True,
                "asr_hotwords_enabled": True,
                "asr_dynamic_endpointing": True,
                "asr_final_refinement_enabled": True,
                "asr_final_refinement_timeout_ms": 1400,
                "asr_final_refinement_min_audio_ms": 320,
                "asr_final_refinement_max_audio_ms": 15000,
                "emotion_enabled": self.settings.emotion_enabled,
                "emotion_deadline_ms": self.settings.emotion_deadline_ms,
            },
            "interaction": {
                "voice_entry_mode": "call",
                "face_to_face_scene": "",
                "idle_continuation_enabled": False,
                "text_idle_seconds": 180,
                "voice_idle_seconds": 30,
                "unlimited_reply_enabled": False,
                "unlimited_reply_interval_seconds": 10,
                "unlimited_reply_max_rounds": 10,
            },
            "capabilities": deepcopy(DEFAULT_CAPABILITY_SETTINGS),
            "appearance": {
                "theme": "mindscape",
                "density": "chat",
                "font_scale": 1.3,
                "language": "zh-CN",
                "sidebar_collapsed": False,
            },
        }

    def _apply_live_settings(self) -> None:
        llm = self._config["llm"]
        audio = self._config["audio"]
        self.settings.llm_mode = str(llm["mode"])
        self.settings.llm_base_url = str(llm["base_url"])
        self.settings.llm_api_key = str(llm["api_key"])
        self.settings.llm_model = str(llm["model"])
        self.settings.llm_context_window = int(llm["context_window"])
        self.settings.context_compaction_enabled = bool(llm["compaction_enabled"])
        self.settings.context_compaction_model = str(llm["compaction_model"])
        self.settings.context_compaction_max_tokens = int(llm["compaction_max_tokens"])
        self.settings.context_compaction_soft_ratio = float(llm["compaction_soft_ratio"])
        self.settings.context_compaction_hard_ratio = float(llm["compaction_hard_ratio"])
        self.settings.context_compaction_patch_limit = int(llm["compaction_patch_limit"])
        self.settings.context_compaction_retain_turns = int(llm["compaction_retain_turns"])
        self.settings.context_compaction_delay_seconds = float(llm["compaction_delay_seconds"])
        self.settings.role_audit_enabled = bool(llm["role_audit_enabled"])
        self.settings.role_audit_model = str(llm["role_audit_model"])
        self.settings.tts_provider = str(audio["tts_provider"])
        self.settings.tts_worker_url = str(audio["tts_worker_url"])
        self.settings.tts_reference_audio = str(audio["tts_reference_audio"])
        self.settings.tts_reference_text = str(audio["tts_reference_text"])
        self.settings.tts_siliconflow_base_url = str(audio["tts_siliconflow_base_url"])
        self.settings.tts_siliconflow_api_key = str(audio["tts_siliconflow_api_key"])
        self.settings.tts_siliconflow_model = str(audio["tts_siliconflow_model"])
        self.settings.tts_siliconflow_voice = str(audio["tts_siliconflow_voice"])
        self.settings.tts_siliconflow_gain = float(audio["tts_siliconflow_gain"])
        self.settings.tts_siliconflow_sample_rate = int(audio["tts_siliconflow_sample_rate"])
        self.settings.tts_gpt_sovits_worker_url = str(audio["tts_gpt_sovits_worker_url"])
        self.settings.tts_gpt_sovits_voice = str(audio["tts_gpt_sovits_voice"])
        self.settings.tts_qwen3_vllm_url = str(audio["tts_qwen3_vllm_url"])
        self.settings.tts_qwen3_vllm_model = str(audio["tts_qwen3_vllm_model"])
        self.settings.tts_qwen3_vllm_voice = str(audio["tts_qwen3_vllm_voice"])
        self.settings.tts_qwen3_vllm_task_type = str(audio["tts_qwen3_vllm_task_type"])
        self.settings.tts_qwen3_vllm_language = str(audio["tts_qwen3_vllm_language"])
        self.settings.auto_tts = bool(audio["auto_tts"])
        self.settings.asr_provider = str(audio["asr_provider"])
        self.settings.asr_base_url = str(audio["asr_base_url"])
        self.settings.asr_api_key = str(audio["asr_api_key"])
        self.settings.asr_model = str(audio["asr_model"])
        self.settings.emotion_enabled = False
        self.settings.emotion_deadline_ms = int(audio["emotion_deadline_ms"])

    def snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        with self._lock:
            value = deepcopy(self._config)
        if redact:
            llm_key = str(value["llm"].get("api_key", ""))
            asr_key = str(value["audio"].get("asr_api_key", ""))
            tts_cloud_key = str(value["audio"].get("tts_siliconflow_api_key", ""))
            value["llm"].pop("api_key", None)
            value["llm"]["credentials_configured"] = bool(llm_key)
            value["audio"].pop("asr_api_key", None)
            value["audio"]["asr_credentials_configured"] = bool(asr_key)
            value["audio"].pop("tts_siliconflow_api_key", None)
            value["audio"]["tts_siliconflow_credentials_configured"] = bool(tts_cloud_key)
            reference = str(value["audio"].pop("tts_reference_audio", "") or "")
            value["audio"]["tts_reference_configured"] = bool(reference)
            value["audio"]["tts_reference_name"] = Path(reference).name if reference else ""
        return value

    def checkpoint(self) -> dict[str, Any]:
        """Return a private rollback snapshot for one in-process settings update."""

        with self._lock:
            return deepcopy(self._config)

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore configuration, disk state and live settings as one compensation step."""

        if not isinstance(snapshot, dict):
            raise ValueError("settings rollback snapshot must be an object")
        with self._lock:
            previous = deepcopy(self._config)
            self._config = deepcopy(snapshot)
            try:
                self._validate()
                _atomic_json(self.path, self._config)
                self._apply_live_settings()
            except Exception:
                self._config = previous
                self._apply_live_settings()
                raise

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("settings patch must be an object")
        with self._lock:
            previous = deepcopy(self._config)
            sanitized = deepcopy(patch)
            llm_patch = sanitized.get("llm")
            if isinstance(llm_patch, dict) and str(llm_patch.get("api_key", "")).strip():
                llm_patch["mode"] = "openai"
            if isinstance(llm_patch, dict) and not llm_patch.get("api_key"):
                llm_patch.pop("api_key", None)
            audio_patch = sanitized.get("audio")
            if isinstance(audio_patch, dict) and not audio_patch.get("asr_api_key"):
                audio_patch.pop("asr_api_key", None)
            if isinstance(audio_patch, dict) and not audio_patch.get("tts_siliconflow_api_key"):
                audio_patch.pop("tts_siliconflow_api_key", None)
            self._config = _merge_known(self._config, sanitized)
            try:
                self._validate()
                _atomic_json(self.path, self._config)
                self._apply_live_settings()
            except Exception as primary_error:
                self._config = previous
                try:
                    _atomic_json(self.path, previous)
                    self._apply_live_settings()
                except Exception as rollback_error:
                    raise RuntimeError(
                        "settings update failed and its local rollback could not be persisted"
                    ) from rollback_error
                raise primary_error
        return self.snapshot(redact=True)

    def _validate(self) -> None:
        persona = self._config["persona"]
        persona["reply_length_preference"] = str(persona.get("reply_length_preference") or "").strip()[:300]
        llm = self._config["llm"]
        llm["mode"] = str(llm["mode"]).strip().lower()
        if llm["mode"] not in {"demo", "openai"}:
            raise ValueError("llm.mode must be 'demo' or 'openai'")
        llm["temperature"] = max(0.0, min(2.0, float(llm["temperature"])))
        llm["max_tokens"] = max(64, min(32768, int(llm["max_tokens"])))
        llm["context_window"] = max(4096, min(2_000_000, int(llm["context_window"])))
        llm["compaction_max_tokens"] = max(256, min(8192, int(llm["compaction_max_tokens"])))
        llm["compaction_soft_ratio"] = max(0.35, min(0.8, float(llm["compaction_soft_ratio"])))
        llm["compaction_hard_ratio"] = max(
            llm["compaction_soft_ratio"] + 0.05,
            min(0.95, float(llm["compaction_hard_ratio"])),
        )
        llm["compaction_patch_limit"] = max(4, min(256, int(llm["compaction_patch_limit"])))
        llm["compaction_retain_turns"] = max(2, min(32, int(llm["compaction_retain_turns"])))
        llm["compaction_delay_seconds"] = max(0.0, min(30.0, float(llm["compaction_delay_seconds"])))
        retrieval = self._config["retrieval"]
        retrieval["knowledge_k"] = max(1, min(50, int(retrieval["knowledge_k"])))
        retrieval["chat_k"] = max(1, min(100, int(retrieval["chat_k"])))
        retrieval["history_k"] = max(1, min(100, int(retrieval.get("history_k", 3))))
        retrieval["similarity_threshold"] = max(0.0, min(1.0, float(retrieval["similarity_threshold"])))
        retrieval["low_exposure_ratio"] = max(0.0, min(0.5, float(retrieval["low_exposure_ratio"])))
        retrieval["memory_family_limit"] = max(1, min(10, int(retrieval["memory_family_limit"])))
        retrieval["starvation_rounds"] = max(1, min(100, int(retrieval["starvation_rounds"])))
        retrieval["starvation_boost"] = max(0.0, min(0.5, float(retrieval["starvation_boost"])))
        retrieval["rrf_k"] = max(1, min(500, int(retrieval["rrf_k"])))
        retrieval["candidate_multiplier"] = max(2, min(12, int(retrieval["candidate_multiplier"])))
        retrieval["max_total_boost"] = max(0.0, min(0.5, float(retrieval["max_total_boost"])))
        retrieval["reranker_top_n"] = max(1, min(50, int(retrieval["reranker_top_n"])))
        for key in (
            "knowledge_user",
            "knowledge_character",
            "knowledge_source",
            "chat_session",
            "chat_exact",
            "chat_text",
        ):
            retrieval["boosts"][key] = max(0.0, min(0.25, float(retrieval["boosts"][key])))
        knowledge = self._config["knowledge"]
        knowledge["child_size"] = max(100, min(3000, int(knowledge["child_size"])))
        knowledge["parent_size"] = max(knowledge["child_size"], min(10000, int(knowledge["parent_size"])))
        knowledge["overlap"] = max(0, min(knowledge["child_size"] - 1, int(knowledge["overlap"])))
        audio = self._config["audio"]
        audio["tts_provider"] = str(audio["tts_provider"]).strip().lower()
        if audio["tts_provider"] not in {
            "browser",
            "mock",
            "cosyvoice",
            "siliconflow",
            "gpt-sovits",
            "qwen3-vllm",
        }:
            raise ValueError(
                "audio.tts_provider must be browser, mock, cosyvoice, siliconflow, gpt-sovits, or qwen3-vllm"
            )
        audio["tts_speed"] = max(0.5, min(2.0, float(audio["tts_speed"])))
        audio["tts_siliconflow_base_url"] = str(audio["tts_siliconflow_base_url"]).strip().rstrip("/")
        audio["tts_siliconflow_model"] = str(audio["tts_siliconflow_model"]).strip()
        audio["tts_siliconflow_voice"] = str(audio["tts_siliconflow_voice"]).strip()
        audio["tts_siliconflow_gain"] = max(-10.0, min(10.0, float(audio["tts_siliconflow_gain"])))
        sample_rate = int(audio["tts_siliconflow_sample_rate"])
        if sample_rate not in {8000, 16000, 24000, 32000, 44100}:
            raise ValueError("unsupported SiliconFlow PCM sample rate")
        audio["tts_siliconflow_sample_rate"] = sample_rate
        audio["tts_gpt_sovits_worker_url"] = str(audio["tts_gpt_sovits_worker_url"]).strip().rstrip("/")
        audio["tts_gpt_sovits_voice"] = str(audio["tts_gpt_sovits_voice"]).strip()
        if audio["tts_gpt_sovits_voice"] not in GPT_SOVITS_VOICES:
            raise ValueError("unsupported GPT-SoVITS voice")
        audio["tts_qwen3_vllm_url"] = str(audio["tts_qwen3_vllm_url"]).strip().rstrip("/")
        if not audio["tts_qwen3_vllm_url"].startswith(("http://", "https://")):
            raise ValueError("audio.tts_qwen3_vllm_url must be an HTTP URL")
        audio["tts_qwen3_vllm_model"] = str(audio["tts_qwen3_vllm_model"]).strip()
        audio["tts_qwen3_vllm_voice"] = str(audio["tts_qwen3_vllm_voice"]).strip().lower()
        task_type = str(audio["tts_qwen3_vllm_task_type"]).strip()
        if task_type.lower() not in {"base", "customvoice"}:
            raise ValueError("audio.tts_qwen3_vllm_task_type must be Base or CustomVoice")
        audio["tts_qwen3_vllm_task_type"] = "Base" if task_type.lower() == "base" else "CustomVoice"
        audio["tts_qwen3_vllm_language"] = str(audio["tts_qwen3_vllm_language"]).strip() or "Chinese"
        if not audio["tts_qwen3_vllm_model"] or not audio["tts_qwen3_vllm_voice"]:
            raise ValueError("Qwen3-TTS model and voice must not be blank")
        audio["asr_silence_ms"] = max(250, min(3000, int(audio["asr_silence_ms"])))
        audio["asr_energy_threshold_db"] = max(-60.0, min(-15.0, float(audio["asr_energy_threshold_db"])))
        audio["asr_noise_gate_db"] = max(-70.0, min(-20.0, float(audio["asr_noise_gate_db"])))
        if audio["asr_noise_gate_db"] > audio["asr_energy_threshold_db"]:
            audio["asr_noise_gate_db"] = audio["asr_energy_threshold_db"]
        audio["asr_min_speech_ms"] = max(80, min(1000, int(audio["asr_min_speech_ms"])))
        audio["asr_listening_energy_threshold_db"] = max(
            -60.0, min(-15.0, float(audio["asr_listening_energy_threshold_db"]))
        )
        audio["asr_listening_min_speech_ms"] = max(60, min(1000, int(audio["asr_listening_min_speech_ms"])))
        audio["asr_barge_in_energy_threshold_db"] = max(
            -60.0, min(-15.0, float(audio["asr_barge_in_energy_threshold_db"]))
        )
        audio["asr_barge_in_min_speech_ms"] = max(120, min(1500, int(audio["asr_barge_in_min_speech_ms"])))
        audio["asr_candidate_release_ms"] = max(80, min(1000, int(audio["asr_candidate_release_ms"])))
        audio["asr_barge_in_cooldown_ms"] = max(250, min(5000, int(audio["asr_barge_in_cooldown_ms"])))
        audio["asr_false_candidate_backoff_ms"] = max(500, min(10000, int(audio["asr_false_candidate_backoff_ms"])))
        audio["asr_duplicate_text_window_ms"] = max(500, min(10000, int(audio["asr_duplicate_text_window_ms"])))
        audio["asr_adaptive_noise_enabled"] = bool(audio["asr_adaptive_noise_enabled"])
        audio["asr_noise_calibration_ms"] = max(500, min(5000, int(audio["asr_noise_calibration_ms"])))
        audio["asr_listening_noise_margin_db"] = max(4.0, min(24.0, float(audio["asr_listening_noise_margin_db"])))
        audio["asr_barge_in_noise_margin_db"] = max(
            audio["asr_listening_noise_margin_db"] + 2.0,
            min(30.0, float(audio["asr_barge_in_noise_margin_db"])),
        )
        audio["asr_utterance_merge_ms"] = max(650, min(3000, int(audio["asr_utterance_merge_ms"])))
        audio["asr_deferred_during_playback"] = bool(audio["asr_deferred_during_playback"])
        audio["asr_hotwords_enabled"] = bool(audio["asr_hotwords_enabled"])
        audio["asr_dynamic_endpointing"] = bool(audio["asr_dynamic_endpointing"])
        audio["asr_final_refinement_enabled"] = bool(audio["asr_final_refinement_enabled"])
        audio["asr_final_refinement_timeout_ms"] = max(200, min(5000, int(audio["asr_final_refinement_timeout_ms"])))
        audio["asr_final_refinement_min_audio_ms"] = max(
            160, min(2000, int(audio["asr_final_refinement_min_audio_ms"]))
        )
        audio["asr_final_refinement_max_audio_ms"] = max(
            audio["asr_final_refinement_min_audio_ms"],
            min(30000, int(audio["asr_final_refinement_max_audio_ms"])),
        )
        # Reserved compatibility field: the emotion implementation is disabled
        # and cannot be re-enabled by stale user configuration.
        audio["emotion_enabled"] = False
        audio["emotion_deadline_ms"] = max(300, min(2500, int(audio["emotion_deadline_ms"])))
        interaction = self._config["interaction"]
        interaction["voice_entry_mode"] = str(interaction["voice_entry_mode"]).strip().lower()
        if interaction["voice_entry_mode"] not in {"call", "face_to_face"}:
            raise ValueError("interaction.voice_entry_mode must be call or face_to_face")
        interaction["face_to_face_scene"] = str(interaction["face_to_face_scene"]).strip()[:2000]
        interaction["idle_continuation_enabled"] = bool(interaction["idle_continuation_enabled"])
        interaction["text_idle_seconds"] = max(10, min(3600, int(interaction["text_idle_seconds"])))
        interaction["voice_idle_seconds"] = max(5, min(600, int(interaction["voice_idle_seconds"])))
        interaction["unlimited_reply_enabled"] = bool(interaction["unlimited_reply_enabled"])
        # Product behavior is intentionally fixed at ten seconds. Keeping the
        # value in the config makes the runtime state explicit without exposing
        # a second timing control in the UI.
        interaction["unlimited_reply_interval_seconds"] = 10
        interaction["unlimited_reply_max_rounds"] = max(1, min(50, int(interaction["unlimited_reply_max_rounds"])))
        capabilities = self._config["capabilities"]
        for key in (
            "master_enabled",
            "local_knowledge_enabled",
            "web_search_enabled",
            "realtime_topics_enabled",
            "topic_expansion_enabled",
            "proactive_hotspots_enabled",
            "show_sources_enabled",
        ):
            capabilities[key] = bool(capabilities[key])
        if not capabilities["web_search_enabled"]:
            capabilities["realtime_topics_enabled"] = False
            capabilities["proactive_hotspots_enabled"] = False
        capabilities["web_timeout_seconds"] = max(2.0, min(30.0, float(capabilities["web_timeout_seconds"])))
        capabilities["max_web_results"] = max(1, min(20, int(capabilities["max_web_results"])))
        capabilities["max_web_pages"] = max(0, min(10, int(capabilities["max_web_pages"])))
        capabilities["max_web_content_chars"] = max(2000, min(30000, int(capabilities["max_web_content_chars"])))
        appearance = self._config["appearance"]
        if appearance["theme"] not in {"mindscape", "dark"}:
            appearance["theme"] = "mindscape"
        appearance["font_scale"] = max(1.0, min(1.6, float(appearance["font_scale"])))
