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
            if str(self._config.get("schema_version") or "") == "1.0.0":
                # 250 ms was the old fixed endpoint and truncated hesitant Chinese.
                # Migrate only that exact legacy default; explicit custom values stay intact.
                if self._config["audio"].get("asr_silence_ms") == 250:
                    self._config["audio"]["asr_silence_ms"] = 600
                self._config["schema_version"] = "1.1.0"
            if loaded != self._config:
                _atomic_json(path, self._config)
        else:
            _atomic_json(path, self._config)
        self._apply_live_settings()

    def _defaults(self) -> dict[str, Any]:
        return {
            "schema_version": "1.1.0",
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
            },
            "retrieval": {
                "rag_enabled": True,
                "knowledge_enabled": True,
                "chat_enabled": True,
                "structured_memory_enabled": True,
                "temporal_enabled": True,
                "bm25_enabled": True,
                "vector_enabled": True,
                "knowledge_k": 5,
                "chat_k": 10,
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
                "tts_speed": 1.0,
                "auto_tts": self.settings.auto_tts,
                "asr_provider": self.settings.asr_provider,
                "asr_base_url": self.settings.asr_base_url,
                "asr_api_key": self.settings.asr_api_key,
                "asr_model": self.settings.asr_model,
                "asr_auto_send": True,
                "asr_silence_ms": 600,
                "asr_energy_threshold_db": -35.0,
                "asr_noise_gate_db": -42.0,
                "asr_min_speech_ms": 120,
                "asr_listening_energy_threshold_db": -36.0,
                "asr_listening_min_speech_ms": 160,
                "asr_barge_in_energy_threshold_db": -27.0,
                "asr_barge_in_min_speech_ms": 420,
                "asr_candidate_release_ms": 280,
                "asr_barge_in_cooldown_ms": 1500,
                "asr_false_candidate_backoff_ms": 3000,
                "asr_duplicate_text_window_ms": 3000,
                "asr_adaptive_noise_enabled": True,
                "asr_noise_calibration_ms": 1500,
                "asr_listening_noise_margin_db": 10.0,
                "asr_barge_in_noise_margin_db": 16.0,
                "asr_utterance_merge_ms": 350,
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
            except (TypeError, ValueError):
                self._config = previous
                raise
            _atomic_json(self.path, self._config)
            self._apply_live_settings()
        return self.snapshot(redact=True)

    def _validate(self) -> None:
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
        llm["compaction_delay_seconds"] = max(
            0.0, min(30.0, float(llm["compaction_delay_seconds"]))
        )
        retrieval = self._config["retrieval"]
        retrieval["knowledge_k"] = max(1, min(50, int(retrieval["knowledge_k"])))
        retrieval["chat_k"] = max(1, min(100, int(retrieval["chat_k"])))
        retrieval["similarity_threshold"] = max(
            0.0, min(1.0, float(retrieval["similarity_threshold"]))
        )
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
        knowledge["parent_size"] = max(
            knowledge["child_size"], min(10000, int(knowledge["parent_size"]))
        )
        knowledge["overlap"] = max(0, min(knowledge["child_size"] - 1, int(knowledge["overlap"])))
        audio = self._config["audio"]
        audio["tts_provider"] = str(audio["tts_provider"]).strip().lower()
        if audio["tts_provider"] not in {
            "browser",
            "mock",
            "cosyvoice",
            "siliconflow",
            "gpt-sovits",
        }:
            raise ValueError(
                "audio.tts_provider must be browser, mock, cosyvoice, siliconflow, or gpt-sovits"
            )
        audio["tts_speed"] = max(0.5, min(2.0, float(audio["tts_speed"])))
        audio["tts_siliconflow_base_url"] = (
            str(audio["tts_siliconflow_base_url"]).strip().rstrip("/")
        )
        audio["tts_siliconflow_model"] = str(audio["tts_siliconflow_model"]).strip()
        audio["tts_siliconflow_voice"] = str(audio["tts_siliconflow_voice"]).strip()
        audio["tts_siliconflow_gain"] = max(-10.0, min(10.0, float(audio["tts_siliconflow_gain"])))
        sample_rate = int(audio["tts_siliconflow_sample_rate"])
        if sample_rate not in {8000, 16000, 24000, 32000, 44100}:
            raise ValueError("unsupported SiliconFlow PCM sample rate")
        audio["tts_siliconflow_sample_rate"] = sample_rate
        audio["tts_gpt_sovits_worker_url"] = (
            str(audio["tts_gpt_sovits_worker_url"]).strip().rstrip("/")
        )
        audio["tts_gpt_sovits_voice"] = str(audio["tts_gpt_sovits_voice"]).strip()
        if audio["tts_gpt_sovits_voice"] not in GPT_SOVITS_VOICES:
            raise ValueError("unsupported GPT-SoVITS voice")
        audio["asr_silence_ms"] = max(250, min(3000, int(audio["asr_silence_ms"])))
        audio["asr_energy_threshold_db"] = max(
            -60.0, min(-15.0, float(audio["asr_energy_threshold_db"]))
        )
        audio["asr_noise_gate_db"] = max(-70.0, min(-20.0, float(audio["asr_noise_gate_db"])))
        if audio["asr_noise_gate_db"] > audio["asr_energy_threshold_db"]:
            audio["asr_noise_gate_db"] = audio["asr_energy_threshold_db"]
        audio["asr_min_speech_ms"] = max(80, min(1000, int(audio["asr_min_speech_ms"])))
        audio["asr_listening_energy_threshold_db"] = max(
            -60.0, min(-15.0, float(audio["asr_listening_energy_threshold_db"]))
        )
        audio["asr_listening_min_speech_ms"] = max(
            60, min(1000, int(audio["asr_listening_min_speech_ms"]))
        )
        audio["asr_barge_in_energy_threshold_db"] = max(
            -60.0, min(-15.0, float(audio["asr_barge_in_energy_threshold_db"]))
        )
        audio["asr_barge_in_min_speech_ms"] = max(
            120, min(1500, int(audio["asr_barge_in_min_speech_ms"]))
        )
        audio["asr_candidate_release_ms"] = max(
            80, min(1000, int(audio["asr_candidate_release_ms"]))
        )
        audio["asr_barge_in_cooldown_ms"] = max(
            250, min(5000, int(audio["asr_barge_in_cooldown_ms"]))
        )
        audio["asr_false_candidate_backoff_ms"] = max(
            500, min(10000, int(audio["asr_false_candidate_backoff_ms"]))
        )
        audio["asr_duplicate_text_window_ms"] = max(
            500, min(10000, int(audio["asr_duplicate_text_window_ms"]))
        )
        audio["asr_adaptive_noise_enabled"] = bool(audio["asr_adaptive_noise_enabled"])
        audio["asr_noise_calibration_ms"] = max(
            500, min(5000, int(audio["asr_noise_calibration_ms"]))
        )
        audio["asr_listening_noise_margin_db"] = max(
            4.0, min(24.0, float(audio["asr_listening_noise_margin_db"]))
        )
        audio["asr_barge_in_noise_margin_db"] = max(
            audio["asr_listening_noise_margin_db"] + 2.0,
            min(30.0, float(audio["asr_barge_in_noise_margin_db"])),
        )
        audio["asr_utterance_merge_ms"] = max(
            300, min(3000, int(audio["asr_utterance_merge_ms"]))
        )
        audio["asr_deferred_during_playback"] = bool(
            audio["asr_deferred_during_playback"]
        )
        audio["asr_hotwords_enabled"] = bool(audio["asr_hotwords_enabled"])
        audio["asr_dynamic_endpointing"] = bool(audio["asr_dynamic_endpointing"])
        audio["asr_final_refinement_enabled"] = bool(
            audio["asr_final_refinement_enabled"]
        )
        audio["asr_final_refinement_timeout_ms"] = max(
            200, min(5000, int(audio["asr_final_refinement_timeout_ms"]))
        )
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
        audio["emotion_deadline_ms"] = max(
            300, min(2500, int(audio["emotion_deadline_ms"]))
        )
        interaction = self._config["interaction"]
        interaction["idle_continuation_enabled"] = bool(
            interaction["idle_continuation_enabled"]
        )
        interaction["text_idle_seconds"] = max(
            10, min(3600, int(interaction["text_idle_seconds"]))
        )
        interaction["voice_idle_seconds"] = max(
            5, min(600, int(interaction["voice_idle_seconds"]))
        )
        interaction["unlimited_reply_enabled"] = bool(
            interaction["unlimited_reply_enabled"]
        )
        # Product behavior is intentionally fixed at ten seconds. Keeping the
        # value in the config makes the runtime state explicit without exposing
        # a second timing control in the UI.
        interaction["unlimited_reply_interval_seconds"] = 10
        interaction["unlimited_reply_max_rounds"] = max(
            1, min(50, int(interaction["unlimited_reply_max_rounds"]))
        )
        capabilities = self._config["capabilities"]
        for key in (
            "master_enabled",
            "local_status_enabled",
            "mindspace_health_enabled",
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
        capabilities["web_timeout_seconds"] = max(
            2.0, min(30.0, float(capabilities["web_timeout_seconds"]))
        )
        capabilities["max_web_results"] = max(
            1, min(20, int(capabilities["max_web_results"]))
        )
        capabilities["max_web_pages"] = max(
            0, min(10, int(capabilities["max_web_pages"]))
        )
        capabilities["max_web_content_chars"] = max(
            2000, min(30000, int(capabilities["max_web_content_chars"]))
        )
        appearance = self._config["appearance"]
        if appearance["theme"] not in {"mindscape", "dark"}:
            appearance["theme"] = "mindscape"
        appearance["font_scale"] = max(1.0, min(1.6, float(appearance["font_scale"])))
