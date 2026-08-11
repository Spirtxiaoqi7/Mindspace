# Mindspace 0.9.0 Per-file Index

> 文档状态：generated。由 `scripts/generate-codebase-index.mjs` 生成；每个维护文件恰好一行。

维护文件总数：**467**。隐藏的 `INDEXED` 标记用于严格 completeness check。

## API composition (11)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/api.test.ts -->
| `frontend/src/api.test.ts` | Web frontend | Verifies consumeEventStream; deduplicates replayed sequences and recognizes terminal events; treats a recovered Core interruption as terminal without retrying; delivers durable model.attempt events without renaming diagnostic fields; desktop settings routing. | none | vitest; ./api; ./types | none | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/AppRouter.tsx -->
| `frontend/src/app/AppRouter.tsx` | Web frontend | Defines AppRouter for the API composition domain. | AppRouter | ../App | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/shared/api.ts -->
| `frontend/src/shared/api.ts` | Web frontend | Defines HttpError; rawRequest; request; apiV1Request; openChatStream for the API composition domain. | HttpError; rawRequest; request; apiV1Request; openChatStream; openRunEventStream; cancelRunRequest; getAudioStatus; consumeEventStream; consumeResumableEventStream | ../types | network | frontend/src/api.test.ts; tests/test_api_route_contract.py; tests/test_api.py | current | Web public; no Core secrets |
<!-- INDEXED:scripts/export-api-contracts.py -->
| `scripts/export-api-contracts.py` | Developer tooling | Deterministically export the Mindspace FastAPI OpenAPI contract. | REPOSITORY_ROOT; SOURCE_ROOT; OUTPUT_PATH; main | __future__; argparse; asyncio; json; sys; tempfile; pathlib; typing | filesystem; database/state | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_082_real_api_regression.py -->
| `scripts/run_082_real_api_regression.py` | Developer tooling | Deprecated 0.8.2 real-provider harness; retained as a fail-closed tombstone. | none | none | database/state | repository policy / full suite | deprecated | Developer tool; release only when allowlisted |
<!-- INDEXED:src/mindspace_graph/api_contracts/__init__.py -->
| `src/mindspace_graph/api_contracts/__init__.py` | Core backend | Public HTTP request and response boundary contracts. | none | mindspace_graph.api_contracts.chat | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api_routes/__init__.py -->
| `src/mindspace_graph/api_routes/__init__.py` | Core backend | Domain route registration for the Mindspace HTTP API. | none | audio_scenes; characters_cards; chat_runs; destiny_routes; legacy_routes; memory_knowledge; system_settings | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api_routes/context.py -->
| `src/mindspace_graph/api_routes/context.py` | Core backend | Defines request DTOs, shared response normalizers and the ApiContext dependency bundle consumed by every route registrar. | ApiContext; request models; normalization helpers | __future__; json; re; dataclasses; pathlib; typing; uuid; httpx | filesystem; database/state; network | tests/test_api.py; tests/test_api_route_contract.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api.py -->
| `src/mindspace_graph/api.py` | Core backend | Creates the FastAPI application, wires shared services and delegates route registration to the split api_routes modules. | create_app; module compatibility exports | __future__; asyncio; time; contextlib; copy; functools; pathlib; typing | filesystem; database/state; network | tests/test_api.py; tests/test_api_route_contract.py | current | Core protected release surface |
<!-- INDEXED:tests/test_api_route_contract.py -->
| `tests/test_api_route_contract.py` | Tests | Verifies test_openapi_operation_set_is_stable_after_route_split; test_static_mount_contract_is_stable_after_route_split; test_multi_method_legacy_tombstones_keep_shared_410_contract. | EXPECTED_OPERATIONS; test_openapi_operation_set_is_stable_after_route_split; test_static_mount_contract_is_stable_after_route_split; test_multi_method_legacy_tombstones_keep_shared_410_contract | __future__; warnings; fastapi.routing; fastapi.testclient; mindspace_graph.api; mindspace_graph.settings | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_api.py -->
| `tests/test_api.py` | Tests | Verifies test_legacy_system_theme_migrates_to_mindscape; test_persisted_demo_mode_migrates_to_real_llm_provider; test_legacy_qwen_preset_migrates_to_locked_custom_voice; test_flat_base_profile_migrates_back_to_expressive_custom_voice; test_appearance_font_scale_defaults_and_is_clamped. | make_settings; test_legacy_system_theme_migrates_to_mindscape; test_persisted_demo_mode_migrates_to_real_llm_provider; test_legacy_qwen_preset_migrates_to_locked_custom_voice; test_flat_base_profile_migrates_back_to_expressive_custom_voice; test_appearance_font_scale_defaults_and_is_clamped; test_voice_phase_thresholds_and_idle_continuation_settings_are_migrated_and_clamped; test_legacy_fixed_endpoint_migrates_without_overwriting_custom_value; test_legacy_fast_voice_merge_window_migrates_without_overwriting_custom_value; test_legacy_voice_sensitivity_defaults_migrate_without_overwriting_custom_value | __future__; json; pathlib; pytest; fastapi.testclient; mindspace_graph.api; mindspace_graph.models; mindspace_graph.product_config | filesystem; database/state; network | direct test file | current | Development-only; not runtime payload by default |

## Audio and scenes (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:src/mindspace_graph/api_routes/audio_scenes.py -->
| `src/mindspace_graph/api_routes/audio_scenes.py` | Core backend | Registers ASR, TTS, voice catalog, scene, journal and presentation-facing endpoints. | register_routes | __future__; asyncio; json; pathlib; typing; uuid; fastapi; fastapi.responses | filesystem | tests/test_audio.py; tests/test_api.py; tests/test_shared_chapters.py | current | Core protected release surface |

## Audio and voice (45)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:config/gpt-sovits-voices.json -->
| `config/gpt-sovits-voices.json` | Governance/config | Defines governed Audio and voice data with top-level keys: schema_version; voices. | schema_version; voices | none | none | repository policy / full suite | current | Development/config; never contains secrets |
<!-- INDEXED:desktop/assets/gpt-sovits-voices.json -->
| `desktop/assets/gpt-sovits-voices.json` | Desktop Launcher | Defines governed Audio and voice data with top-level keys: schema_version; voices. | schema_version; voices | none | none | repository policy / full suite | generated | Launcher public; no protected Core source |
<!-- INDEXED:desktop/voice-controller.cjs -->
| `desktop/voice-controller.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports; IPC launcher:voice | node:fs; node:path; ./gpt-sovits-catalog.cjs; ./onboarding-policy.cjs | filesystem; Electron IPC | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:docs/ASR_FINAL_REFINEMENT.md -->
| `docs/ASR_FINAL_REFINEMENT.md` | Documentation | Documents “中文 ASR 整句复核与调度” with historical authority. | 中文 ASR 整句复核与调度 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/GPT-SOVITS-VOICE-CATALOG.md -->
| `docs/GPT-SOVITS-VOICE-CATALOG.md` | Documentation | Documents “GPT-SoVITS 人物音色目录” with historical authority. | GPT-SoVITS 人物音色目录 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/QWEN3_TTS_RUNTIME.md -->
| `docs/QWEN3_TTS_RUNTIME.md` | Documentation | Documents “Qwen3 实时语音运行时” with prototype authority. | Qwen3 实时语音运行时 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/VOICE_INTERACTION_MODES.md -->
| `docs/VOICE_INTERACTION_MODES.md` | Documentation | Documents “语音通话与面对面互动” with prototype authority. | 语音通话与面对面互动 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/voice-session-architecture.md -->
| `docs/voice-session-architecture.md` | Documentation | Documents “语音会话架构（0.5.39）” with prototype authority. | 语音会话架构（0.5.39） | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:frontend/public/tts-playback-worklet.js -->
| `frontend/public/tts-playback-worklet.js` | Web frontend | class MindspaceTTSPlaybackProcessor extends AudioWorkletProcessor { | none | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/index.ts -->
| `frontend/src/features/voice/index.ts` | Web frontend | Maintains Audio and voice configuration or execution behavior. | none | ../../speech; ./VoiceMode; ./useTtsRuntime; ./useVoiceSessionRuntime; ../../types; ./useAsrReadiness; ./types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/types.ts -->
| `frontend/src/features/voice/types.ts` | Web frontend | export interface SpeechQueueItem { | SpeechQueueItem; PCMStreamHandle; VoiceCaptureGraph; WarmVoiceCapture | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/useAsrReadiness.ts -->
| `frontend/src/features/voice/useAsrReadiness.ts` | Web frontend | Defines useAsrReadiness for the Audio and voice domain. | useAsrReadiness | react; ../../shared/api; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/useTtsRuntime.ts -->
| `frontend/src/features/voice/useTtsRuntime.ts` | Web frontend | Defines alignPCM16Chunk; shouldBufferQwenReplyForSinglePass; shouldAutomaticallyQueueSpeech; shouldSkipSpeechSegmentFailure; TtsRuntimeCallbacks for the Audio and voice domain. | alignPCM16Chunk; shouldBufferQwenReplyForSinglePass; shouldAutomaticallyQueueSpeech; shouldSkipSpeechSegmentFailure; TtsRuntimeCallbacks; useTtsRuntime | react; ../../shared/api; ../../shared/formatters; ../../speech; ../../types; ./types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/useVoiceSessionRuntime.ts -->
| `frontend/src/features/voice/useVoiceSessionRuntime.ts` | Web frontend | Defines companionContinuationPlan; voiceMergeDelay; shouldIgnoreASREvent; voiceReconnectDelay; shouldRetryMicrophoneStartup for the Audio and voice domain. | companionContinuationPlan; voiceMergeDelay; shouldIgnoreASREvent; voiceReconnectDelay; shouldRetryMicrophoneStartup; asrClientDisposition; VoiceSessionRuntimeCallbacks; useVoiceSessionRuntime | react; ../../shared/api; ../../shared/formatters; ../../shared/turn; ../../types; ./types; ./useTtsRuntime | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/voice/VoiceMode.tsx -->
| `frontend/src/features/voice/VoiceMode.tsx` | Web frontend | Defines VoiceMode for the Audio and voice domain. | VoiceMode | react; ../../types; ../../ui/avatar | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/microphone-capture.test.ts -->
| `frontend/src/microphone-capture.test.ts` | Web frontend | Verifies microphone capture; keeps microphone capture independent from TTS playback; does not close the shared capture context from a stale start attempt; keeps ASR open while generation and TTS preparation are pending; parks a disabled live track for a bounded rapid-reopen window. | none | node:fs; node:path; vitest | filesystem | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:scripts/audit-gpt-sovits-voices.py -->
| `scripts/audit-gpt-sovits-voices.py` | Developer tooling | Audit the curated GPT-SoVITS voice archives without downloading whole ZIP files. | REPOSITORY; RESOLVE_BASE; FILES_API; REQUESTED; DISPLAY_FRANCHISE; HttpRangeReader; fetch_json; decode_zip_name; normal_name; inspect_archive | __future__; argparse; datetime; io; json; re; sys; urllib.parse | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/benchmark-asr-final.py -->
| `scripts/benchmark-asr-final.py` | Developer tooling | Measure local streaming/final ASR load, VRAM and warm single-utterance latency. | main | __future__; argparse; json; pathlib; time; numpy; soundfile; torch | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/download-asr-models.py -->
| `scripts/download-asr-models.py` | Developer tooling | Resumable, checksum-verified ModelScope downloader for the FunASR stack. | CORE_MODELS; OPTIONAL_MODELS; MODELS; API_ROOT; FILE_ROOT; main | __future__; argparse; hashlib; json; os; pathlib; urllib.parse; urllib.request | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/extract-voice-archive.py -->
| `scripts/extract-voice-archive.py` | Developer tooling | Safely extract third-party voice archives with deterministic legacy-name decoding. | safe_target; extract_zip; extract_tar; main | __future__; argparse; tarfile; zipfile; pathlib | filesystem | tests/test_extract_voice_archive.py | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/prepare-asr.ps1 -->
| `scripts/prepare-asr.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/prepare-qwen3-tts.ps1 -->
| `scripts/prepare-qwen3-tts.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/prepare-tts.ps1 -->
| `scripts/prepare-tts.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/qwen3-tts-base.yaml -->
| `scripts/qwen3-tts-base.yaml` | Developer tooling | Mindspace Qwen3-TTS Base deployment. | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/requirements-tts-runtime.txt -->
| `scripts/requirements-tts-runtime.txt` | Developer tooling | conformer==0.3.2 | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/smoke-asr.py -->
| `scripts/smoke-asr.py` | Developer tooling | Stream the bundled example WAV through the real FunASR WebSocket worker. | run; main | __future__; argparse; asyncio; json; os; wave; pathlib; websockets.asyncio.client | filesystem; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/start-asr.ps1 -->
| `scripts/start-asr.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/start-qwen3-tts.ps1 -->
| `scripts/start-qwen3-tts.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/start-tts.ps1 -->
| `scripts/start-tts.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/test-asr-final-e2e.ps1 -->
| `scripts/test-asr-final-e2e.ps1` | Developer tooling | [CmdletBinding()] | none | none | process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/validate-qwen-voice-consistency.py -->
| `scripts/validate-qwen-voice-consistency.py` | Developer tooling | Measure whether Qwen TTS samples keep the same speaker identity. | parse_args; main | __future__; argparse; json; itertools; pathlib; librosa; soundfile; torch | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:src/mindspace_graph/asr_vocabulary.py -->
| `src/mindspace_graph/asr_vocabulary.py` | Core backend | Deterministic ASR vocabulary compiled from manual entries and profile JSON. | PRIORITIES; MANAGED_FIELDS; SKIP_KEYS; TERM_SPLIT; LATIN_TERM; SYSTEM_ENTRIES; ASRVocabularyStore | __future__; hashlib; json; re; copy; datetime; pathlib; threading | filesystem | tests/test_asr_vocabulary.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/asr_worker.py -->
| `src/mindspace_graph/asr_worker.py` | Core backend | Dedicated FunASR process so model loading never blocks the LangGraph API. | create_worker_app; main | __future__; argparse; asyncio; json; os; time; array; contextlib | environment | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/audio.py -->
| `src/mindspace_graph/audio.py` | Core backend | Service-oriented TTS/ASR adapters with browser fallbacks and cancellation. | AudioProviderUnavailable; qwen3_request_seed; sanitize_tts_text; is_speakable_tts_text; AudioService | __future__; asyncio; hashlib; json; mimetypes; time; wave; collections.abc | filesystem; network | tests/test_audio.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/native_microphone.py -->
| `src/mindspace_graph/native_microphone.py` | Core backend | Resident native microphone capture for the local ASR worker. | InputEndpoint; select_input_endpoints; select_input_device; NativeMicrophoneCapture | __future__; asyncio; json; os; threading; time; array; dataclasses | filesystem; environment | tests/test_native_microphone.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/streaming_asr.py -->
| `src/mindspace_graph/streaming_asr.py` | Core backend | Low-latency PCM streaming ASR with FunASR and deterministic fallbacks. | STOP_COMMANDS; FILLER_ONLY; apply_asr_decision; ASRSessionOptions; GPUInferenceScheduler; ASRTextCorrector; apply_final_refinement; FunASRRuntime; FunASRStreamSession | __future__; math; re; contextlib; dataclasses; difflib; importlib.util; io | none | tests/test_streaming_asr_noise.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/voice_render.py -->
| `src/mindspace_graph/voice_render.py` | Core backend | Deterministic voice-render metadata for streamed companion replies. | VOICE_CUES; ADULT_VOICE_CUES; DEFAULT_VOICE_CUE; normalize_voice_cue; extract_voice_cue; infer_qwen3_voice_cue; qwen3_instructions; pace_qwen3_base_text; VoiceCueStream | __future__; re; dataclasses | none | tests/test_voice_render.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/web/tts-playback-worklet.js -->
| `src/mindspace_graph/web/tts-playback-worklet.js` | Core backend | class MindspaceTTSPlaybackProcessor extends AudioWorkletProcessor { | none | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:tests/test_asr_vocabulary.py -->
| `tests/test_asr_vocabulary.py` | Tests | Verifies test_profile_json_is_compiled_without_prompt_or_llm; test_private_r18_protocol_is_not_compiled_into_asr_hotwords; test_manual_correction_is_atomic_and_immediately_testable; test_record_correction_reuses_existing_target; test_asr_observation_is_bounded_metadata_outside_profiles. | test_profile_json_is_compiled_without_prompt_or_llm; test_private_r18_protocol_is_not_compiled_into_asr_hotwords; test_manual_correction_is_atomic_and_immediately_testable; test_record_correction_reuses_existing_target; test_asr_observation_is_bounded_metadata_outside_profiles | __future__; mindspace_graph.adapters.file_storage; mindspace_graph.asr_vocabulary | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_audio.py -->
| `tests/test_audio.py` | Tests | Verifies test_tts_text_removes_nested_parenthetical_directions; test_tts_text_drops_unclosed_parenthetical_tail; test_tts_text_rejects_punctuation_only_fragments; test_streaming_tts_rejects_punctuation_before_opening_worker_stream; test_siliconflow_payload_uses_raw_streaming_pcm. | test_tts_text_removes_nested_parenthetical_directions; test_tts_text_drops_unclosed_parenthetical_tail; test_tts_text_rejects_punctuation_only_fragments; test_streaming_tts_rejects_punctuation_before_opening_worker_stream; test_siliconflow_payload_uses_raw_streaming_pcm; test_siliconflow_payload_requires_api_key; test_local_tts_requests_are_serialized_before_reaching_worker; test_qwen3_custom_voice_payload_locks_speaker_seed_and_one_style_instruction; test_qwen3_seed_is_stable_per_reply_and_voice; test_qwen3_requests_use_the_same_single_synthesis_queue | asyncio; pytest; mindspace_graph.audio; mindspace_graph.settings | network | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_extract_voice_archive.py -->
| `tests/test_extract_voice_archive.py` | Tests | Verifies test_extract_tar_rejects_path_escape; test_extract_zip_writes_regular_files. | ROOT; SPEC; MODULE; test_extract_tar_rejects_path_escape; test_extract_zip_writes_regular_files | __future__; importlib.util; io; tarfile; zipfile; pathlib; pytest | filesystem | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_native_microphone.py -->
| `tests/test_native_microphone.py` | Tests | Verifies test_select_input_device_prefers_windows_mme; test_endpoint_selection_prefers_wasapi_native_rate_and_remembers_success; test_native_capture_resamples_wasapi_block_to_asr_rate; test_select_input_device_accepts_sounddevice_input_output_pair; test_select_input_device_explains_when_windows_has_no_input. | FakeInputStream; FakeInputOutputPair; fake_sounddevice; test_select_input_device_prefers_windows_mme; test_endpoint_selection_prefers_wasapi_native_rate_and_remembers_success; test_native_capture_resamples_wasapi_block_to_asr_rate; test_select_input_device_accepts_sounddevice_input_output_pair; test_select_input_device_explains_when_windows_has_no_input; test_resident_capture_fans_out_pcm_and_stays_open; test_capture_disables_without_opening_device | __future__; asyncio; sys; time; types; mindspace_graph.native_microphone | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_streaming_asr_noise.py -->
| `tests/test_streaming_asr_noise.py` | Tests | Verifies test_low_confidence_playback_text_remains_draft_only; test_short_vad_confirmed_playback_phrase_can_interrupt_after_duration_check; test_tts_echo_is_rejected_even_when_vad_detects_a_voice; test_explicit_stop_is_fast_barge_in_but_still_requires_vad; test_uncommon_name_disagreement_keeps_only_the_reliable_backbone. | test_low_confidence_playback_text_remains_draft_only; test_short_vad_confirmed_playback_phrase_can_interrupt_after_duration_check; test_tts_echo_is_rejected_even_when_vad_detects_a_voice; test_explicit_stop_is_fast_barge_in_but_still_requires_vad; test_uncommon_name_disagreement_keeps_only_the_reliable_backbone; test_runtime_waits_for_an_in_progress_preload; test_runtime_serializes_shared_model_inference; test_gpu_scheduler_gives_waiting_stream_priority_over_final_pass; test_breath_like_noise_below_threshold_does_not_start_speech; test_short_loud_transient_does_not_start_speech | __future__; math; sys; threading; time; concurrent.futures; types; numpy | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_voice_render.py -->
| `tests/test_voice_render.py` | Tests | Verifies test_voice_cue_is_removed_from_completed_spoken_text; test_qwen3_base_pacing_slows_semantic_boundaries_without_changing_words; test_qwen3_base_pacing_preserves_existing_breath_and_paragraph_pause; test_invalid_voice_cue_falls_back_to_neutral_without_leaking_a_tag; test_adult_delivery_preset_is_only_accepted_when_the_turn_allows_it. | test_voice_cue_is_removed_from_completed_spoken_text; test_qwen3_base_pacing_slows_semantic_boundaries_without_changing_words; test_qwen3_base_pacing_preserves_existing_breath_and_paragraph_pause; test_invalid_voice_cue_falls_back_to_neutral_without_leaking_a_tag; test_adult_delivery_preset_is_only_accepted_when_the_turn_allows_it; test_streamed_split_voice_cue_is_held_until_the_prefix_is_complete; test_non_voice_text_keeps_low_latency_and_neutral_uses_natural_conversation_hint; test_style_instruction_is_short_positive_and_does_not_repeat_format_rules; test_alluring_is_close_and_low_energy_not_an_excited_default; test_custom_voice_infers_only_prosody_while_requested_cue_wins | mindspace_graph.voice_render | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:vendor/cosyvoice_mindspace_worker.py -->
| `vendor/cosyvoice_mindspace_worker.py` | Packaging adapter | Resident CosyVoice worker for Mindspace TTS. | CosyVoiceWorker; make_handler; main | __future__; argparse; hashlib; json; os; socket; sys; threading | filesystem; environment | repository policy / full suite | current | Core protected release surface |

## Characters and V2 cards (18)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:docs/CHARACTER_ART_LIBRARY.md -->
| `docs/CHARACTER_ART_LIBRARY.md` | Documentation | Documents “典藏卡册美术资源规范” with prototype authority. | 典藏卡册美术资源规范 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/CHARACTER_CARD_PACKAGE.md -->
| `docs/CHARACTER_CARD_PACKAGE.md` | Documentation | Documents “`.mindspace-card` 角色卡包格式” with prototype authority. | `.mindspace-card` 角色卡包格式 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MULTI_CHARACTER_ARCHITECTURE.md -->
| `docs/MULTI_CHARACTER_ARCHITECTURE.md` | Documentation | Documents “Mindspace 0.6 多角色架构” with prototype authority. | Mindspace 0.6 多角色架构 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:frontend/src/features/characters/index.ts -->
| `frontend/src/features/characters/index.ts` | Web frontend | Maintains Characters and V2 cards configuration or execution behavior. | none | ../../characters/CharacterExperience; ./ProfileCardDialogView; ./useCharacterDirectory; ../../ui/avatar; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/characters/ProfileCardDialogView.tsx -->
| `frontend/src/features/characters/ProfileCardDialogView.tsx` | Web frontend | Defines ProfileCardDialog for the Characters and V2 cards domain. | ProfileCardDialog | react; ../../shared/api; ../../shared/formatters; ../../shared/Modal; ../../types; ../../ui/avatar | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/characters/useCharacterDirectory.ts -->
| `frontend/src/features/characters/useCharacterDirectory.ts` | Web frontend | Defines useCharacterDirectory for the Characters and V2 cards domain. | useCharacterDirectory | react; ../../shared/api; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/profile/index.ts -->
| `frontend/src/features/profile/index.ts` | Web frontend | export { ProfileDialog } from "./ProfileDialog"; | none | ./ProfileDialog | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/profile/ProfileDialog.tsx -->
| `frontend/src/features/profile/ProfileDialog.tsx` | Web frontend | Defines ProfileDialog for the Characters and V2 cards domain. | ProfileDialog | react; ../../shared/Modal; ../../shared/api; ../../shared/formatters; ../../types; ../../ui/styledConfirm | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:scripts/generate-character-voice-reference.py -->
| `scripts/generate-character-voice-reference.py` | Developer tooling | Generate stable Qwen3-TTS VoiceDesign references for Mindspace. | REFERENCE_TEXT; VOICE_CANDIDATES; parse_args; main | __future__; argparse; json; pathlib; time; soundfile; torch; qwen_tts | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/precompute-qwen-character-voice.py -->
| `scripts/precompute-qwen-character-voice.py` | Developer tooling | Convert one approved reference WAV into a reusable Qwen3-TTS Base profile. | DEFAULT_VOICE_NAME; VALIDATION_TEXTS; parse_args; sha256_file; speaker_embedding; main | __future__; argparse; hashlib; json; pathlib; librosa; numpy; qwen_tts | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:src/mindspace_graph/adapters/profile_repository.py -->
| `src/mindspace_graph/adapters/profile_repository.py` | Core backend | File-backed canonical profile repository. | DEFAULT_PROFILES; TARGET_FILES; JsonProfileRepository | __future__; json; re; shutil; copy; datetime; pathlib; threading | filesystem; database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api_routes/characters_cards.py -->
| `src/mindspace_graph/api_routes/characters_cards.py` | Core backend | Registers character library, V2 card, avatar, session and profile compatibility endpoints. | register_routes | __future__; asyncio; hashlib; io; json; zipfile; pathlib; typing | filesystem; database/state | tests/test_characters.py; tests/test_api.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/character_card.py -->
| `src/mindspace_graph/character_card.py` | Core backend | Mindspace's compact SillyTavern Character Card V2 authority model. | CARD_SPEC; CARD_VERSION; CARD_FIELDS; empty_memory; normalize_memory; normalize_tasks_v2; normalize_appearance; appearance_summary; description_with_appearance; normalize_card | __future__; re; copy; datetime; typing; uuid | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/characters.py -->
| `src/mindspace_graph/characters.py` | Core backend | V2 character library with one-time migration for historical profile records. | CHARACTER_SCHEMA_VERSION; MIGRATION_KEY; V2_MIGRATION_KEY; TASKS_V2_MIGRATION_KEY; LEGACY_CHARACTER_ID; SAFE_ID; SOURCES; STATUSES; CharacterRepository | __future__; json; re; shutil; copy; datetime; pathlib; threading | filesystem; database/state | tests/test_characters.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/profile_bootstrap.py -->
| `src/mindspace_graph/profile_bootstrap.py` | Core backend | Deterministic three-turn profile bootstrap detection. | BOOTSTRAP_EMPTY_THRESHOLD; BOOTSTRAP_MAX_ROUNDS; BOOTSTRAP_MAX_FIELDS; BOOTSTRAP_MAX_LEAF_PATCHES; ProfileBootstrap; evaluate_profile_bootstrap | __future__; dataclasses; typing; mindspace_graph.memory_registry; mindspace_graph.models | none | tests/test_profile_bootstrap.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/profile_schema.py -->
| `src/mindspace_graph/profile_schema.py` | Core backend | Versioned, forward-compatible validation for authoritative profile JSON. | PROFILE_TYPES; IDENTITY_GENDERS; REQUIRED_SECTIONS; ProfileSchemaRegistry; DEFAULT_PROFILE_SCHEMA | __future__; json; math; copy; typing; mindspace_graph.memory_registry | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:tests/test_characters.py -->
| `tests/test_characters.py` | Tests | Verifies test_legacy_profile_migration_is_idempotent_and_binds_sessions; test_character_migration_rolls_back_database_and_keeps_backup; test_legacy_character_creation_routes_are_gone; test_v2_session_character_binding_rejects_silent_rebind; test_v2_card_export_import_generates_new_id_and_excludes_private_data. | make_settings; v2_card; create_v2_character; test_legacy_profile_migration_is_idempotent_and_binds_sessions; test_character_migration_rolls_back_database_and_keeps_backup; test_legacy_character_creation_routes_are_gone; test_v2_session_character_binding_rejects_silent_rebind; test_v2_card_export_import_generates_new_id_and_excludes_private_data; test_v2_legacy_task_titles_migrate_to_stable_structured_tasks; test_task_commands_are_revisioned_and_idempotent | __future__; io; json; zipfile; copy; pytest; fastapi.testclient; mindspace_graph.adapters.file_storage | filesystem; database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_profile_bootstrap.py -->
| `tests/test_profile_bootstrap.py` | Tests | Verifies test_bootstrap_is_server_enabled_only_in_first_three_turns_when_sparse; test_bootstrap_accepts_up_to_eight_fill_only_setup_patches; test_bootstrap_deterministically_drops_paraphrases_not_present_in_setup; test_bootstrap_rejects_overwrite_and_closes_on_fourth_round; test_prompt_never_exposes_profile_write_bootstrap_to_the_chat_model. | profiles; request; test_bootstrap_is_server_enabled_only_in_first_three_turns_when_sparse; test_bootstrap_accepts_up_to_eight_fill_only_setup_patches; test_bootstrap_deterministically_drops_paraphrases_not_present_in_setup; test_bootstrap_rejects_overwrite_and_closes_on_fourth_round; test_prompt_never_exposes_profile_write_bootstrap_to_the_chat_model | __future__; copy; mindspace_graph.adapters.file_storage; mindspace_graph.models; mindspace_graph.policies; mindspace_graph.profile_bootstrap; mindspace_graph.prompting | none | direct test file | current | Development-only; not runtime payload by default |

## Chat and durable runs (18)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/chat-contract.test.ts -->
| `frontend/src/chat-contract.test.ts` | Web frontend | Verifies chat request persistence and regeneration; persists every prompt-affecting field while marking attachment bodies for reattachment; replays a complete request without replacing its modes or prompt controls; blocks regeneration when an attachment body is unavailable; attachment guardrails. | none | vitest; ./chat-contract; ./types | none | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat-contract.ts -->
| `frontend/src/chat-contract.ts` | Web frontend | Defines ACTIVE_RUN_STORAGE_KEY; ActiveRunRecord; AttachmentMergeResult; RegenerationPreparation; requestAttachments for the Chat and durable runs domain. | ACTIVE_RUN_STORAGE_KEY; ActiveRunRecord; AttachmentMergeResult; RegenerationPreparation; requestAttachments; hasMissingAttachmentContent; mergeAttachmentFiles; sanitizeTurnRequest; createActiveRunRecord; writeActiveRun | ./types | network | frontend/src/chat-contract.test.ts | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/Composer.test.tsx -->
| `frontend/src/chat/Composer.test.tsx` | Web frontend | Verifies shows voice only when live ASR is ready and switches to send for payload; keeps missing attachment metadata visible and removable before regeneration. | none | @testing-library/react; @testing-library/user-event; vitest; ./Composer | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/ExecutionInspector.test.tsx -->
| `frontend/src/chat/ExecutionInspector.test.tsx` | Web frontend | Verifies renders logical calls and provider attempts from durable inspector events. | none | @testing-library/react; vitest; ./ExecutionInspector; ../types | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/MessageList.tsx -->
| `frontend/src/chat/MessageList.tsx` | Web frontend | Defines MessageListProps; MessageList for the Chat and durable runs domain. | MessageListProps; MessageList | react; ../chat-contract; ../types; ../ui/avatar; ./ExecutionInspector | UI/rendering | frontend/src/MessageList.test.tsx | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/useConversation.test.tsx -->
| `frontend/src/chat/useConversation.test.tsx` | Web frontend | Verifies recovers a durable active run through the same resumable SSE path. | none | @testing-library/react; vitest; ../chat-contract; ../types; ./useConversation | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/chatRuntimeBridge.ts -->
| `frontend/src/features/chat/chatRuntimeBridge.ts` | Web frontend | Defines clearPersistedChatRun; restoreSessionMessages; getProviderToolCapability; createModelAttemptInspectorEvent; createModelSummaryInspectorEvent for the Chat and durable runs domain. | clearPersistedChatRun; restoreSessionMessages; getProviderToolCapability; createModelAttemptInspectorEvent; createModelSummaryInspectorEvent | ../../chat-contract; ../../shared/formatters; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/ChatWorkspace.tsx -->
| `frontend/src/features/chat/ChatWorkspace.tsx` | Web frontend | Defines ChatNavigationViewModel; ChatConversationViewModel; ChatComposerViewModel; ChatOverlayViewModel; ChatWorkspace for the Chat and durable runs domain. | ChatNavigationViewModel; ChatConversationViewModel; ChatComposerViewModel; ChatOverlayViewModel; ChatWorkspace | react; ../../chat/Composer; ../../chat/MessageList; ../../shared/formatters; ../../types; ../../ui/avatar | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/index.ts -->
| `frontend/src/features/chat/index.ts` | Web frontend | export { Composer } from "../../chat/Composer"; | none | ../../chat/Composer; ../../chat/ExecutionInspector; ../../chat/MessageList; ../../chat/useConversation; ./useSessionDirectory; ./ChatWorkspace; ./useChatRuntime; ./useTurnComposer | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/useChatRuntime.ts -->
| `frontend/src/features/chat/useChatRuntime.ts` | Web frontend | Defines ChatRuntimeCallbacks; useChatRuntime for the Chat and durable runs domain. | ChatRuntimeCallbacks; useChatRuntime | react; ../../chat/useConversation; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/useConversationMaintenance.ts -->
| `frontend/src/features/chat/useConversationMaintenance.ts` | Web frontend | Defines useConversationMaintenance for the Chat and durable runs domain. | useConversationMaintenance | react; ../../shared/api; ../../chat-contract; ../../types; ../../ui/styledConfirm | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/useSessionDirectory.ts -->
| `frontend/src/features/chat/useSessionDirectory.ts` | Web frontend | Defines useSessionDirectory for the Chat and durable runs domain. | useSessionDirectory | react; ../../shared/api; ../../shared/formatters; ../../types; ../../ui/styledConfirm | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/chat/useTurnComposer.ts -->
| `frontend/src/features/chat/useTurnComposer.ts` | Web frontend | Defines TurnComposerEffects; useTurnComposer for the Chat and durable runs domain. | TurnComposerEffects; useTurnComposer | react; ../../chat-contract; ../../shared/formatters; ../../shared/turn; ../../types; ./useChatRuntime | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/MessageList.test.tsx -->
| `frontend/src/MessageList.test.tsx` | Web frontend | Verifies keeps the product behavior where opening More immediately references the message. | none | @testing-library/react; @testing-library/user-event; vitest; ./chat/MessageList; ./types | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/api_contracts/chat.py -->
| `src/mindspace_graph/api_contracts/chat.py` | Core backend | Public HTTP contracts for chat endpoints. | ChatTurnCreateRequest | __future__; datetime; typing; pydantic; pydantic.json_schema; mindspace_graph; mindspace_graph.models | none | frontend/src/chat-contract.test.ts; frontend/src/chat/Composer.test.tsx; frontend/src/chat/ExecutionInspector.test.tsx; frontend/src/chat/useConversation.test.tsx | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api_routes/chat_runs.py -->
| `src/mindspace_graph/api_routes/chat_runs.py` | Core backend | Registers synchronous chat, streaming chat, durable run event replay, cancellation and interruption endpoints. | register_routes | __future__; fastapi; fastapi.responses; mindspace_graph.api_contracts.chat; mindspace_graph.models; context | database/state | tests/test_chat_execution_state_machine.py; tests/test_api.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/conversation_runs.py -->
| `src/mindspace_graph/conversation_runs.py` | Core backend | Persists durable conversation runs and ordered SSE envelopes, supports replay, join, completion and orphan recovery. | ConversationRunRepository; BufferedStreamRun; StreamEnvelopeFactory; decode_sse | __future__; asyncio; json; time; collections; collections.abc; dataclasses; datetime | database/state | tests/test_chat_execution_state_machine.py; tests/test_api.py | current | Core protected release surface |
<!-- INDEXED:tests/test_chat_execution_state_machine.py -->
| `tests/test_chat_execution_state_machine.py` | Tests | Verifies test_sync_chat_reuses_completed_durable_run_and_returns_null_tool_execution; test_concurrent_duplicate_joins_one_graph_and_executes_tool_once; test_provider_final_failure_is_audited_and_recoverable_from_durable_events; test_new_service_terminalizes_orphaned_running_run_without_reexecution. | CountingModel; test_sync_chat_reuses_completed_durable_run_and_returns_null_tool_execution; NativeToolModel; CountingWebCapability; test_concurrent_duplicate_joins_one_graph_and_executes_tool_once; test_provider_final_failure_is_audited_and_recoverable_from_durable_events; test_new_service_terminalizes_orphaned_running_run_without_reexecution | __future__; asyncio; unittest.mock; httpx; pytest; fastapi.testclient; mindspace_graph.adapters.in_memory; mindspace_graph.adapters.openai_compatible | database/state; network | direct test file | current | Development-only; not runtime payload by default |

## Chat orchestration (2)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:src/mindspace_graph/graph.py -->
| `src/mindspace_graph/graph.py` | Core backend | Builds the single-turn LangGraph and its conditional native-tool execution path. | build_graph | __future__; typing; langgraph.graph; mindspace_graph.nodes; mindspace_graph.ports; mindspace_graph.state | none | tests/test_graph.py; tests/test_capabilities.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/service.py -->
| `src/mindspace_graph/service.py` | Core backend | Coordinates a conversation turn across profiles, retrieval, LangGraph execution, persistence, compaction and post-turn work. | none | mindspace_graph.application.conversation; mindspace_graph.bootstrap | none | tests/test_graph.py; tests/test_prompt_cache_layout.py; tests/test_chat_execution_state_machine.py | current | Core protected release surface |

## Core foundation (62)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:.dockerignore -->
| `.dockerignore` | Repository root | Maintains Core foundation configuration or execution behavior. | none | none | none | desktop/announcement-policy.test.cjs; desktop/app-paths.test.cjs; desktop/bootstrap-core.test.cjs; desktop/companion-assets.test.cjs | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:.gitignore -->
| `.gitignore` | Repository root | Python environments and caches | none | none | database/state | desktop/announcement-policy.test.cjs; desktop/app-paths.test.cjs; desktop/bootstrap-core.test.cjs; desktop/companion-assets.test.cjs | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:.gitmodules -->
| `.gitmodules` | Repository root | [submodule "vendor/CosyVoice"] | none | none | none | desktop/announcement-policy.test.cjs; desktop/app-paths.test.cjs; desktop/bootstrap-core.test.cjs; desktop/companion-assets.test.cjs | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:CHANGELOG.md -->
| `CHANGELOG.md` | Documentation | Documents “更新历史” with current authority. | 更新历史 | linked current/historical documentation | none | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:docker-compose.yml -->
| `docker-compose.yml` | Repository root | Maintains Core foundation configuration or execution behavior. | none | none | none | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:Dockerfile -->
| `Dockerfile` | Repository root | FROM python:3.11.15-slim | none | none | none | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:payload.json -->
| `payload.json` | Repository root | Defines governed Core foundation data with top-level keys: targets; schema_version; requires_dependency_sync; version. | targets; schema_version; requires_dependency_sync; version | none | none | repository policy / full suite | generated | Core release manifest/dependency surface |
<!-- INDEXED:pyproject.toml -->
| `pyproject.toml` | Repository root | Maintains Core foundation configuration or execution behavior. | none | none | network | repository policy / full suite | current | Core release manifest/dependency surface |
<!-- INDEXED:README.md -->
| `README.md` | Documentation | Documents “Mindspace 0.9.0” with current authority. | Mindspace 0.9.0 | linked current/historical documentation | database/state | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:SECURITY.md -->
| `SECURITY.md` | Documentation | Documents “Security Policy” with current authority. | Security Policy | linked current/historical documentation | none | desktop/security-boundaries.test.cjs | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:src/mindspace_graph/__init__.py -->
| `src/mindspace_graph/__init__.py` | Core backend | Mindspace package with lazy exports for lightweight audio workers. | none | __future__; typing | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/__init__.py -->
| `src/mindspace_graph/adapters/__init__.py` | Core backend | Concrete adapters for local demos and OpenAI-compatible model endpoints. | none | none | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/file_storage.py -->
| `src/mindspace_graph/adapters/file_storage.py` | Core backend | Compatibility exports for legacy file-storage repository imports. | none | mindspace_graph.adapters.profile_repository; mindspace_graph.adapters.session_repository; mindspace_graph.infrastructure.storage.json_io; mindspace_graph.infrastructure.storage.json_patch | database/state | tests/test_file_storage.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/json_audit.py -->
| `src/mindspace_graph/adapters/json_audit.py` | Core backend | Append-only JSONL audit adapter. | JsonlAudit | __future__; json; datetime; pathlib; threading; typing | filesystem | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/session_repository.py -->
| `src/mindspace_graph/adapters/session_repository.py` | Core backend | File-backed chat session repository. | JsonSessionRepository | __future__; json; re; shutil; copy; datetime; pathlib; threading | filesystem; database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/admin_cli.py -->
| `src/mindspace_graph/admin_cli.py` | Core backend | Offline maintenance commands for deterministic data repair. | main | __future__; argparse; json; pathlib; mindspace_graph.service; mindspace_graph.settings | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/application/__init__.py -->
| `src/mindspace_graph/application/__init__.py` | Core backend | Mindspace application services. | none | mindspace_graph.application.conversation | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/application/conversation.py -->
| `src/mindspace_graph/application/conversation.py` | Core backend | Conversation application service and durable streaming orchestration. | NODE_LABELS; ConversationService | __future__; asyncio; time; collections.abc; typing; uuid; mindspace_graph.cancellation; mindspace_graph.compaction | database/state | frontend/src/chat/useConversation.test.tsx | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/application/turn_preparation.py -->
| `src/mindspace_graph/application/turn_preparation.py` | Core backend | Prepare client chat input with authoritative server-side turn state. | RETRIEVAL_INDEX_ONLY_ROUNDS; TurnPreparationService | __future__; re; collections.abc; datetime; typing; mindspace_graph.models; mindspace_graph.ports; mindspace_graph.role_runtime | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/art_catalog.py -->
| `src/mindspace_graph/art_catalog.py` | Core backend | Versioned art catalog and resumable optional-pack installer. | ArtPackPaused; ArtCatalogService | __future__; hashlib; ipaddress; json; shutil; socket; threading; zipfile | filesystem; network | tests/test_art_catalog.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/bootstrap.py -->
| `src/mindspace_graph/bootstrap.py` | Core backend | Application composition root and concrete adapter assembly. | ProductContainer; build_container | __future__; dataclasses; pathlib; mindspace_graph.adapters.profile_repository; mindspace_graph.adapters.session_repository; mindspace_graph.adapters.in_memory; mindspace_graph.adapters.json_audit; mindspace_graph.adapters.local_retriever | database/state | desktop/bootstrap-core.test.cjs; tests/test_profile_bootstrap.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/cancellation.py -->
| `src/mindspace_graph/cancellation.py` | Core backend | Thread-safe cancellation state shared by HTTP handlers and graph nodes. | GenerationCancelled; CancellationRegistry | __future__; threading | none | tests/test_cancellation.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/cli.py -->
| `src/mindspace_graph/cli.py` | Core backend | Local entry point that runs without API keys or external services. | main | __future__; argparse; json; mindspace_graph.adapters.in_memory; mindspace_graph.graph; mindspace_graph.models | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/context_ledger.py -->
| `src/mindspace_graph/context_ledger.py` | Core backend | Durable append-only model context and background compaction bookkeeping. | AUDIT_ONLY_KINDS; EPHEMERAL_KINDS; MODEL_HISTORY_ROUNDS; MODEL_DIALOGUE_KINDS; summary_prompt_view; authoritative_profile_message; authoritative_patch_message; ContextSnapshot; CompactionJob; ContextLedger | __future__; hashlib; json; re; sqlite3; contextlib; dataclasses; datetime | filesystem; database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/emotion_disabled.py -->
| `src/mindspace_graph/emotion_disabled.py` | Core backend | Dormant emotion adapter kept behind the public port for future reactivation. | DisabledEmotionCoordinator | __future__; typing | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/emotion.py -->
| `src/mindspace_graph/emotion.py` | Core backend | Serializable emotion contracts retained while the capability is disabled. | AudioQuality; TextEmotionState; ResponseGuidance; FusedEmotionState; EmotionState | __future__; datetime; typing; pydantic | none | tests/test_emotion_sidechain.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/entity_registry.py -->
| `src/mindspace_graph/entity_registry.py` | Core backend | Deterministic entity identity and explicitly curated aliases. | normalize_entity; EntityRegistry | __future__; hashlib; re; unicodedata; typing; mindspace_graph.product_database | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/gpt_sovits.py -->
| `src/mindspace_graph/gpt_sovits.py` | Core backend | Deterministic GPT-SoVITS voice catalog and installed-file checks. | GPT_SOVITS_VOICES; voice_definition; voice_paths; voice_is_installed; public_voice_catalog | __future__; json; pathlib; typing | none | tests/test_gpt_sovits_catalog.py; tests/test_gpt_sovits_worker.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/__init__.py -->
| `src/mindspace_graph/infrastructure/__init__.py` | Core backend | Shared infrastructure primitives for Mindspace adapters. | none | none | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/storage/__init__.py -->
| `src/mindspace_graph/infrastructure/storage/__init__.py` | Core backend | Storage infrastructure shared by concrete repositories. | none | mindspace_graph.infrastructure.storage.json_io; mindspace_graph.infrastructure.storage.json_patch; mindspace_graph.infrastructure.storage.metadata; mindspace_graph.infrastructure.storage.paths | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/storage/json_io.py -->
| `src/mindspace_graph/infrastructure/storage/json_io.py` | Core backend | Low-level JSON file operations shared by storage adapters. | atomic_json_write; read_json | __future__; json; os; tempfile; pathlib; typing | filesystem | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/storage/json_patch.py -->
| `src/mindspace_graph/infrastructure/storage/json_patch.py` | Core backend | JSON Pointer and Patch operations shared by file-backed repositories. | json_pointer_tokens; read_json_pointer; apply_json_patch | __future__; copy; typing | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/storage/metadata.py -->
| `src/mindspace_graph/infrastructure/storage/metadata.py` | Core backend | Metadata primitives shared by file-backed repositories. | utc_now_iso | __future__; datetime | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/infrastructure/storage/paths.py -->
| `src/mindspace_graph/infrastructure/storage/paths.py` | Core backend | Deterministic path helpers shared by file-backed repositories. | safe_json_stem; hashed_json_document_path; legacy_json_document_path | __future__; hashlib; pathlib; typing | database/state | desktop/app-paths.test.cjs | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/models.py -->
| `src/mindspace_graph/models.py` | Core backend | Validated boundary models for one conversational turn. | ApiConfig; RetrievalSettings; ASRUncertainSegment; ASRInputEvidence; InputEvidence; VoiceInteractionContext; ActivityPromptContext; ScenePromptContext; ChatInteraction; ChatAttachment | __future__; hashlib; json; datetime; typing; uuid; pydantic | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/nodes.py -->
| `src/mindspace_graph/nodes.py` | Core backend | Small, testable LangGraph nodes for the Mindspace turn lifecycle. | build_contextual_retrieval_query; should_open_adult_continuity; NodeFactory | __future__; re; time; typing; langgraph.types; mindspace_graph.cancellation; mindspace_graph.models; mindspace_graph.native_tools | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/policies.py -->
| `src/mindspace_graph/policies.py` | Core backend | Deterministic trust, retrieval, and JSON write policies. | MAX_PATCHES_PER_TURN; REVISION_KEYS; rank_with_temporal_decay; normalize_json_update; sanitize_profile_bootstrap; validate_json_update | __future__; json; math; re; datetime; typing; mindspace_graph.entity_registry; mindspace_graph.memory_registry | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/ports.py -->
| `src/mindspace_graph/ports.py` | Core backend | Ports isolate workflow decisions from storage, retrieval, and model vendors. | RetrieverPort; ChatCorpusPort; StructuredMemoryPort; ProfileRepositoryPort; SessionRepositoryPort; LanguageModelPort; LanguageModelFactoryPort; RolePolicyPort; AuditPort; CancellationPort | __future__; collections.abc; dataclasses; typing; mindspace_graph.context_ledger; mindspace_graph.entity_registry; mindspace_graph.models; mindspace_graph.product_database | database/state | desktop/service-ports.test.cjs | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/product_database.py -->
| `src/mindspace_graph/product_database.py` | Core backend | Shared SQLite unit-of-work and JSON document persistence. | ProductDatabase | __future__; json; sqlite3; collections.abc; contextlib; contextvars; datetime; pathlib | filesystem; database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompt_blocks.py -->
| `src/mindspace_graph/prompt_blocks.py` | Core backend | Immutable prompt blocks and deterministic final-message compilation. | PromptBlock; PromptCompiler | __future__; dataclasses; typing | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompt_contributors.py -->
| `src/mindspace_graph/prompt_contributors.py` | Core backend | Concrete contributors for deterministic prompt-message assembly. | StaticPrefixContributor; PrefixContributor; RetrievalContributor; HistoryTimeIndexContributor; RecentHistoryContributor; DynamicTailContributor; build_static_prompt_messages; compile_prompt_messages | __future__; dataclasses; typing; mindspace_graph.prompt_blocks; mindspace_graph.prompt_templates | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompt_event_templates.py -->
| `src/mindspace_graph/prompt_event_templates.py` | Core backend | Pure text templates for dynamic prompt events and voice controls. | build_voice_enabled_control_line; build_qwen3_tts_control_lines; build_streaming_voice_control_lines; build_voice_disabled_control_line; build_voice_delivery_control_lines; build_direct_response_control_line; build_idle_continuation_control_line; build_continuous_companionship_control_line; build_initiative_control_line; build_unconfirmed_state_control_line | __future__ | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompt_inspection.py -->
| `src/mindspace_graph/prompt_inspection.py` | Core backend | Short-lived, read-only inspection of the exact messages sent to the main model. | PromptInspectionStore | __future__; hashlib; json; time; collections; copy; threading; typing | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompt_templates.py -->
| `src/mindspace_graph/prompt_templates.py` | Core backend | Pure model-visible prompt text templates without runtime dependencies. | build_persona_template; build_contract_template; build_authoritative_state_template; build_physical_time_control_lines; build_history_time_index_template; build_quick_interaction_template; build_reply_context_template; build_attachment_item_template; build_attachments_template; join_turn_data_templates | __future__ | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/prompting.py -->
| `src/mindspace_graph/prompting.py` | Core backend | Role-first prompt assembly kept separate from orchestration and model I/O. | PromptBuild; split_history_for_cache; resolve_initiative_request; build_prompt; build_messages | __future__; json; re; copy; dataclasses; datetime; typing; zoneinfo | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/protocol.py -->
| `src/mindspace_graph/protocol.py` | Core backend | Parser for the four-block protocol already used by Mindspace. | TRAILING_MODEL_TOKEN; LEADING_VOICE_DIRECTIVE; IncrementalResponseParser; ProtocolParser | __future__; json; re; typing; pydantic; mindspace_graph.models | none | tests/test_streaming_protocol.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/r18_director.py -->
| `src/mindspace_graph/r18_director.py` | Core backend | Compact product-level adult-scene state and expression guidance. | explicit_r18_requested; resolve_scene_state; build_style_packet; r18_quality_requirement | __future__; re; typing; mindspace_graph.models | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/r18_private_library.py -->
| `src/mindspace_graph/r18_private_library.py` | Core backend | Read-only, locally packaged R18 source material. | RESOURCE_PATH; seal_payload; unseal_payload; load_private_r18_material; private_library_status | __future__; hashlib; hmac; io; os; re; zipfile; functools | environment | tests/test_r18_private_library.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/role_audit.py -->
| `src/mindspace_graph/role_audit.py` | Core backend | Non-blocking semantic role audit scheduled after the visible turn completes. | AUDIT_SYSTEM; parse_role_audit; RoleAuditService | __future__; json; re; collections.abc; threading; typing; mindspace_graph.context_ledger; mindspace_graph.models | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/roleplay.py -->
| `src/mindspace_graph/roleplay.py` | Core backend | Deterministic roleplay context, retrieval admission, and style-quality helpers. | classify_roleplay_turn; companion_lane; resolve_presentation_mode; effective_roleplay_temperature; effective_roleplay_max_tokens; project_history_for_presentation; build_presentation_plan; select_roleplay_examples; build_scene_packet; build_roleplay_layer | __future__; re; difflib; typing; mindspace_graph.models; mindspace_graph.r18_director; mindspace_graph.voice_render | none | tests/test_roleplay.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/server.py -->
| `src/mindspace_graph/server.py` | Core backend | Console entry point for the packaged product server. | main | __future__; uvicorn; mindspace_graph.settings | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/shared_chapters.py -->
| `src/mindspace_graph/shared_chapters.py` | Core backend | Shared-chapter entertainment data without weakening profile authority. | JournalCreate; JournalUpdate; MomentCreate; MomentUpdate; ActivityStart; ActivityAction; SceneSelectionUpdate; ACTIVITY_DEFINITIONS; LEGACY_SCENE_ACTIVITY; SCENE_LOCATION_LABELS | __future__; hashlib; json; re; collections.abc; copy; datetime; typing | database/state | tests/test_shared_chapters.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/state.py -->
| `src/mindspace_graph/state.py` | Core backend | The explicit, serializable state passed between LangGraph nodes. | TurnState | __future__; operator; typing; typing_extensions; mindspace_graph.emotion; mindspace_graph.models; mindspace_graph.profile_bootstrap; mindspace_graph.tool_chain | none | tests/test_chat_execution_state_machine.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/web/archive/interactions.css -->
| `src/mindspace_graph/web/archive/interactions.css` | Core backend | @keyframes mindspace-card-lift { | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/web/archive/manifest.json -->
| `src/mindspace_graph/web/archive/manifest.json` | Core backend | Defines governed Core foundation data with top-level keys: schema_version; library_id; license; approval_status; defaults; categories; packs; preview_index. | schema_version; library_id; license; approval_status; defaults; categories; packs; preview_index; assets | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/web/archive/previews/index.json -->
| `src/mindspace_graph/web/archive/previews/index.json` | Core backend | Defines governed Core foundation data with top-level keys: schema_version; approval_status; items; expanded_library. | schema_version; approval_status; items; expanded_library | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/web/archive/previews/review.html -->
| `src/mindspace_graph/web/archive/previews/review.html` | Core backend | <!doctype html> | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/web/index.html -->
| `src/mindspace_graph/web/index.html` | Core backend | <!doctype html> | none | none | UI/rendering | repository policy / full suite | generated | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/web/pcm-worklet.js -->
| `src/mindspace_graph/web/pcm-worklet.js` | Core backend | class MindspacePCMProcessor extends AudioWorkletProcessor { | none | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:THIRD_PARTY_NOTICES.md -->
| `THIRD_PARTY_NOTICES.md` | Documentation | Documents “第三方组件说明” with current authority. | 第三方组件说明 | linked current/historical documentation | none | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:uv.lock -->
| `uv.lock` | Repository root | Maintains Core foundation configuration or execution behavior. | none | none | network | repository policy / full suite | generated | Core release manifest/dependency surface |
<!-- INDEXED:vendor/gpt_sovits_mindspace_worker.py -->
| `vendor/gpt_sovits_mindspace_worker.py` | Packaging adapter | Resident, switchable GPT-SoVITS worker for Mindspace. | GPTSoVITSWorker; make_handler; parse_args; main | __future__; argparse; json; os; sys; threading; time; wave | filesystem; environment | repository policy / full suite | current | Core protected release surface |

## Desktop bridge (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:desktop/preload.cjs -->
| `desktop/preload.cjs` | Desktop Launcher | Exposes the narrow renderer IPC bridge for settings, updates, diagnostics, storage and window operations. | none | electron | Electron IPC | desktop/security-boundaries.test.cjs; desktop/settings-bridge.test.cjs | current | Launcher public; no protected Core source |

## Desktop composition (31)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:desktop/announcement-policy.cjs -->
| `desktop/announcement-policy.cjs` | Desktop Launcher | function shouldAutoOpenAnnouncement(update, shownRelease = "") { | module.exports | none | none | desktop/announcement-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/app-paths.cjs -->
| `desktop/app-paths.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path; ./storage-location.cjs | filesystem; environment | desktop/app-paths.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/bootstrap-core.cjs -->
| `desktop/bootstrap-core.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path; extract-zip | filesystem | desktop/bootstrap-core.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/companion-controller.cjs -->
| `desktop/companion-controller.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports; IPC companion:snapshot; IPC companion:action | node:fs; node:path; electron; ./companion-policy.cjs | filesystem; Electron IPC; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/companion-policy.cjs -->
| `desktop/companion-policy.cjs` | Desktop Launcher | const DEFAULT_WIDTH = 336; | module.exports | none | none | desktop/companion-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/component-manager.cjs -->
| `desktop/component-manager.cjs` | Desktop Launcher | const crypto = require("node:crypto"); | module.exports | node:crypto; node:fs; node:path; ./gpt-sovits-catalog.cjs | filesystem; database/state | desktop/component-manager.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/diagnostics-controller.cjs -->
| `desktop/diagnostics-controller.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/environment-registry.cjs -->
| `desktop/environment-registry.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/external-navigation.cjs -->
| `desktop/external-navigation.cjs` | Desktop Launcher | const DEFAULT_ALLOWED_HOSTS = Object.freeze(new Set([ | module.exports | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/gpt-sovits-catalog.cjs -->
| `desktop/gpt-sovits-catalog.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem | tests/test_gpt_sovits_catalog.py | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/hardware-policy.cjs -->
| `desktop/hardware-policy.cjs` | Desktop Launcher | const GIB = 1024 ** 3; | module.exports | none | none | desktop/hardware-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/index.html -->
| `desktop/index.html` | Desktop Launcher | <!doctype html> | none | none | UI/rendering | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/main.cjs -->
| `desktop/main.cjs` | Desktop Launcher | Composes Electron lifecycle, windows, preload security, service supervision, settings and update controllers. | IPC launcher:snapshot; IPC launcher:service; IPC launcher:all; IPC launcher:open; IPC launcher:external; IPC launcher:maintenance; IPC launcher:shortcut; IPC runtime:diagnostics | electron; node:child_process; node:crypto; node:fs; node:path; extract-zip; ./component-manager.cjs; ./companion-controller.cjs | filesystem; network; Electron IPC; process execution; environment | desktop/desktop-architecture.test.cjs; desktop/security-boundaries.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/onboarding-controller.cjs -->
| `desktop/onboarding-controller.cjs` | Desktop Launcher | const { ONBOARDING_VERSION, deriveOnboardingSnapshot, normalizeVoicePreference, voicePreferenceFromProvider } = require("./onboarding-policy.cjs"); | module.exports; IPC launcher:onboarding | ./onboarding-policy.cjs | network; Electron IPC | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/onboarding-policy.cjs -->
| `desktop/onboarding-policy.cjs` | Desktop Launcher | const ONBOARDING_VERSION = 2; | module.exports | none | none | desktop/onboarding-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/prepare-bootstrap.cjs -->
| `desktop/prepare-bootstrap.cjs` | Desktop Launcher | const crypto = require("node:crypto"); | none | node:crypto; node:fs; node:path | filesystem | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/private-core-smoke.cjs -->
| `desktop/private-core-smoke.cjs` | Desktop Launcher | const { spawn, spawnSync } = require("node:child_process"); | none | node:child_process; node:fs; node:path | filesystem; network; process execution; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/process-check.cjs -->
| `desktop/process-check.cjs` | Desktop Launcher | const { spawn } = require("node:child_process"); | module.exports | node:child_process | process execution; environment | desktop/process-check.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/product-windows.cjs -->
| `desktop/product-windows.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path; ./external-navigation.cjs | filesystem; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/qwen-controller.cjs -->
| `desktop/qwen-controller.cjs` | Desktop Launcher | const { spawn } = require("node:child_process"); | module.exports | node:child_process; node:fs; node:path; ./qwen-runtime-policy.cjs | filesystem; process execution; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/root-hint.json -->
| `desktop/root-hint.json` | Desktop Launcher | Defines governed Desktop composition data with top-level keys: root. | root | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/service-policy.cjs -->
| `desktop/service-policy.cjs` | Desktop Launcher | const SERVICE_START_ORDER = Object.freeze(["api", "asr", "tts"]); | module.exports | none | none | desktop/service-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/service-ports.cjs -->
| `desktop/service-ports.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem; environment | desktop/service-ports.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/src/main.tsx -->
| `desktop/src/main.tsx` | Desktop Launcher | Maintains Desktop composition configuration or execution behavior. | none | react; react-dom/client | filesystem; UI/rendering | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/src/redesign.css -->
| `desktop/src/redesign.css` | Desktop Launcher | Defines the Desktop composition visual stylesheet and responsive presentation rules. | none | none | UI/rendering | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/src/styles.css -->
| `desktop/src/styles.css` | Desktop Launcher | Defines the Desktop composition visual stylesheet and responsive presentation rules. | none | none | UI/rendering | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/src/vite-env.d.ts -->
| `desktop/src/vite-env.d.ts` | Desktop Launcher | <reference types="vite/client" /> | none | none | filesystem | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/storage-controller.cjs -->
| `desktop/storage-controller.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports; IPC launcher:select-root; IPC launcher:select-storage; IPC launcher:migrate-recommended-storage | node:fs; node:path; ./app-paths.cjs; ./bootstrap-core.cjs; ./storage-location.cjs | Electron IPC; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/storage-location.cjs -->
| `desktop/storage-location.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem; database/state; environment | desktop/storage-location.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/tsconfig.json -->
| `desktop/tsconfig.json` | Desktop Launcher | Defines governed Desktop composition data with top-level keys: compilerOptions; include. | compilerOptions; include | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/vite.config.ts -->
| `desktop/vite.config.ts` | Desktop Launcher | Maintains Desktop composition configuration or execution behavior. | none | vite; @vitejs/plugin-react | none | repository policy / full suite | current | Launcher public; no protected Core source |

## Desktop controllers (3)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:desktop/service-supervisor.cjs -->
| `desktop/service-supervisor.cjs` | Desktop Launcher | Starts, observes and boundedly restarts Core and optional voice services using the shared port registry. | module.exports | node:fs; node:path; node:net; node:child_process | filesystem; network; process execution; environment | desktop/service-policy.test.cjs; desktop/service-ports.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/settings-controller.cjs -->
| `desktop/settings-controller.cjs` | Desktop Launcher | Coordinates Core-first settings writes with encrypted provider secret persistence and rollback-safe status reporting. | module.exports; IPC launcher:settings-save; IPC launcher:settings-get | ./secret-store.cjs | network; Electron IPC | desktop/settings-bridge.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/update-controller.cjs -->
| `desktop/update-controller.cjs` | Desktop Launcher | Coordinates signed Core and Launcher update checks, progress, installation and recovery policies. | module.exports; IPC launcher:update | node:crypto; node:path; ./update-manager.cjs; ./launcher-updater.cjs | Electron IPC | desktop/update-manager.test.cjs; desktop/launcher-updater.test.cjs | current | Launcher public; no protected Core source |

## Documentation governance (42)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:docs/APPLICATION_ALGORITHM_FOUNDATION.md -->
| `docs/APPLICATION_ALGORITHM_FOUNDATION.md` | Documentation | Documents “Mindspace 应用层算法根基（v1）” with prototype authority. | Mindspace 应用层算法根基（v1） | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/APPLICATION_FULL_CHAIN.md -->
| `docs/APPLICATION_FULL_CHAIN.md` | Documentation | Documents “Mindspace 0.9.0 全链路” with current authority. | Mindspace 0.9.0 全链路 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE_BACKEND.md -->
| `docs/ARCHITECTURE_BACKEND.md` | Documentation | Documents “Mindspace 后端架构” with current authority. | Mindspace 后端架构 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE_DESKTOP.md -->
| `docs/ARCHITECTURE_DESKTOP.md` | Documentation | Documents “Mindspace Desktop Architecture” with current authority. | Mindspace Desktop Architecture | linked current/historical documentation | database/state; Electron IPC; process execution | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE_FRONTEND.md -->
| `docs/ARCHITECTURE_FRONTEND.md` | Documentation | Documents “Mindspace 前端架构” with current authority. | Mindspace 前端架构 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE_PROMPTS.md -->
| `docs/ARCHITECTURE_PROMPTS.md` | Documentation | Documents “Mindspace Prompt 架构” with current authority. | Mindspace Prompt 架构 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE_STORAGE.md -->
| `docs/ARCHITECTURE_STORAGE.md` | Documentation | Documents “Mindspace 存储架构” with current authority. | Mindspace 存储架构 | linked current/historical documentation | database/state | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ARCHITECTURE.md -->
| `docs/ARCHITECTURE.md` | Documentation | Documents “Mindspace 只读拆解与 LangGraph 映射” with historical authority. | Mindspace 只读拆解与 LangGraph 映射 | linked current/historical documentation | none | desktop/desktop-architecture.test.cjs | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ART_PREVIEW_PROVENANCE_0.7.0.md -->
| `docs/ART_PREVIEW_PROVENANCE_0.7.0.md` | Documentation | Documents “0.7.0 共同篇章美术预览来源” with historical authority. | 0.7.0 共同篇章美术预览来源 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/CODE_READING_GUIDE.md -->
| `docs/CODE_READING_GUIDE.md` | Documentation | Documents “0.9.0 代码阅读指南” with current authority. | 0.9.0 代码阅读指南 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/CODEBASE_FILE_INDEX_0.9.0.md -->
| `docs/CODEBASE_FILE_INDEX_0.9.0.md` | Documentation | Documents “Mindspace 0.9.0 Per-file Index” with generated authority. | Mindspace 0.9.0 Per-file Index | linked current/historical documentation | database/state; network; process execution | repository policy / full suite | generated | Development-only; not runtime payload by default |
<!-- INDEXED:docs/CODEBASE_INDEX_0.9.0.md -->
| `docs/CODEBASE_INDEX_0.9.0.md` | Documentation | Documents “Mindspace 0.9.0 Codebase Index” with generated authority. | Mindspace 0.9.0 Codebase Index | linked current/historical documentation | none | repository policy / full suite | generated | Development-only; not runtime payload by default |
<!-- INDEXED:docs/DEPRECATION_REGISTER_0.9.0.md -->
| `docs/DEPRECATION_REGISTER_0.9.0.md` | Documentation | Documents “0.9.0 废弃清单” with current authority. | 0.9.0 废弃清单 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/DEVELOPMENT_DESIGN_HISTORY.md -->
| `docs/DEVELOPMENT_DESIGN_HISTORY.md` | Documentation | Documents “Mindspace 整体设计演进与开发记录” with historical authority. | Mindspace 整体设计演进与开发记录 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/DEVELOPMENT_WORKFLOW_0.9.0.md -->
| `docs/DEVELOPMENT_WORKFLOW_0.9.0.md` | Documentation | Documents “0.9.0 分支与提交规范” with current authority. | 0.9.0 分支与提交规范 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/EMOTION_INTERFACE.md -->
| `docs/EMOTION_INTERFACE.md` | Documentation | Documents “情绪能力接口（暂时停用）” with prototype authority. | 情绪能力接口（暂时停用） | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ENGINEER_HANDBOOK.md -->
| `docs/ENGINEER_HANDBOOK.md` | Documentation | Documents “Mindspace 工程师手册” with historical authority. | Mindspace 工程师手册 | linked current/historical documentation | database/state; process execution | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/FRONTEND_REFERENCES.md -->
| `docs/FRONTEND_REFERENCES.md` | Documentation | Documents “前端参考与取舍” with prototype authority. | 前端参考与取舍 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/GENDER_IDENTITY.md -->
| `docs/GENDER_IDENTITY.md` | Documentation | Documents “用户与 AI 的第一认同性别” with prototype authority. | 用户与 AI 的第一认同性别 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/IMPLEMENTATION_PLAN.md -->
| `docs/IMPLEMENTATION_PLAN.md` | Documentation | Documents “实施规划与完成状态” with prototype authority. | 实施规划与完成状态 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/INDEX.md -->
| `docs/INDEX.md` | Documentation | Documents “Mindspace 文档状态索引” with current authority. | Mindspace 文档状态索引 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/LAUNCHER_ONBOARDING.md -->
| `docs/LAUNCHER_ONBOARDING.md` | Documentation | Documents “启动器首次引导与语音安装策略” with prototype authority. | 启动器首次引导与语音安装策略 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/LIVE2D_COMPANION_RESOURCE_BUDGET.md -->
| `docs/LIVE2D_COMPANION_RESOURCE_BUDGET.md` | Documentation | Documents “Live2D 桌宠接入前环境与开销核验” with prototype authority. | Live2D 桌宠接入前环境与开销核验 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/LLM_JSON_ORCHESTRATION.md -->
| `docs/LLM_JSON_ORCHESTRATION.md` | Documentation | Documents “LLM Prompt 与 JSON 决策编排” with prototype authority. | LLM Prompt 与 JSON 决策编排 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/LOCAL_REPORT_POLICY.md -->
| `docs/LOCAL_REPORT_POLICY.md` | Documentation | Documents “本地报告与真实 API 证据规范” with current authority. | 本地报告与真实 API 证据规范 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MATURITY_HARDENING.md -->
| `docs/MATURITY_HARDENING.md` | Documentation | Documents “七项成熟化改造说明” with historical authority. | 七项成熟化改造说明 | linked current/historical documentation | database/state | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MIGRATION_ROLLBACK_0.6.0.md -->
| `docs/MIGRATION_ROLLBACK_0.6.0.md` | Documentation | Documents “0.6.0 迁移与回滚” with historical authority. | 0.6.0 迁移与回滚 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MINDSPACE_0.5.49_INSTALLATION_VERIFICATION.md -->
| `docs/MINDSPACE_0.5.49_INSTALLATION_VERIFICATION.md` | Documentation | Documents “Mindspace 0.5.49 安装验收记录” with historical authority. | Mindspace 0.5.49 安装验收记录 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MINDSPACE_0.5.49_PRODUCT_DELIVERY_PLAN.md -->
| `docs/MINDSPACE_0.5.49_PRODUCT_DELIVERY_PLAN.md` | Documentation | Documents “Mindspace 0.5.49 产品交付与安装方案” with historical authority. | Mindspace 0.5.49 产品交付与安装方案 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MINDSPACE_0.8.3_CODE_AUDIT_STAGE_1.md -->
| `docs/MINDSPACE_0.8.3_CODE_AUDIT_STAGE_1.md` | Documentation | Documents “Mindspace 0.8.3 代码大审查：第一阶段” with report authority. | Mindspace 0.8.3 代码大审查：第一阶段 | linked current/historical documentation | database/state | repository policy / full suite | report | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MINDSPACE_FUNCTION_MAP.md -->
| `docs/MINDSPACE_FUNCTION_MAP.md` | Documentation | Documents “Mindspace 0.9.0 功能图” with current authority. | Mindspace 0.9.0 功能图 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MODULAR_ARCHITECTURE.md -->
| `docs/MODULAR_ARCHITECTURE.md` | Documentation | Documents “Mindspace 模块化单体边界” with current authority. | Mindspace 模块化单体边界 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/MODULAR_REFACTOR_VALIDATION_0.9.0.md -->
| `docs/MODULAR_REFACTOR_VALIDATION_0.9.0.md` | Documentation | Documents “Mindspace 0.9.0 模块化重构验收报告” with report authority. | Mindspace 0.9.0 模块化重构验收报告 | linked current/historical documentation | network | repository policy / full suite | report | Development-only; not runtime payload by default |
<!-- INDEXED:docs/PACKAGING.md -->
| `docs/PACKAGING.md` | Documentation | Documents “封装与分包方案” with current authority. | 封装与分包方案 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/PRODUCT_ARCHITECTURE.md -->
| `docs/PRODUCT_ARCHITECTURE.md` | Documentation | Documents “产品架构” with prototype authority. | 产品架构 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/PRODUCT_INTRODUCTION.md -->
| `docs/PRODUCT_INTRODUCTION.md` | Documentation | Documents “Mindspace 产品介绍与用户指南” with prototype authority. | Mindspace 产品介绍与用户指南 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/prototypes/launcher-onboarding-v2.html -->
| `docs/prototypes/launcher-onboarding-v2.html` | Documentation | <!doctype html> | none | none | UI/rendering | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/r18-style-library.md -->
| `docs/r18-style-library.md` | Documentation | Documents “R18 style library and Director” with prototype authority. | R18 style library and Director | linked current/historical documentation | database/state | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/readme/ASSETS.md -->
| `docs/readme/ASSETS.md` | Documentation | Documents “README 展示资源说明” with current authority. | README 展示资源说明 | linked current/historical documentation | none | desktop/companion-assets.test.cjs; desktop/runtime-assets.test.cjs | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/roleplay-card-v2.md -->
| `docs/roleplay-card-v2.md` | Documentation | Documents “Mindspace 角色卡 V2 与长期 RAG 边界” with prototype authority. | Mindspace 角色卡 V2 与长期 RAG 边界 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/SHARED_CHAPTERS_ARCHITECTURE.md -->
| `docs/SHARED_CHAPTERS_ARCHITECTURE.md` | Documentation | Documents “Mindspace 0.7.2 共同篇章与会话场景架构” with historical authority. | Mindspace 0.7.2 共同篇章与会话场景架构 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/VERIFICATION.md -->
| `docs/VERIFICATION.md` | Documentation | Documents “0.9.0 验证门禁” with current authority. | 0.9.0 验证门禁 | linked current/historical documentation | database/state | repository policy / full suite | current | Development-only; not runtime payload by default |

## Frontend characters (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/characters/CharacterExperience.tsx -->
| `frontend/src/characters/CharacterExperience.tsx` | Web frontend | Presents mode lobby, character picker/library and navigation into V7 creation or local chat. | ModeLobby; CharacterPicker; CharacterLibrary | react; ../shared/api; ../types | UI/rendering | frontend/src/App.test.tsx; frontend/src/DestinyCanvas.test.tsx | current | Web public; no Core secrets |

## Frontend chat (3)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/chat/Composer.tsx -->
| `frontend/src/chat/Composer.tsx` | Web frontend | Renders the composer, attachments, model switch, ASR/send action and multi-select interaction tags. | Composer | react; ../chat-contract; ../types | UI/rendering | frontend/src/chat/Composer.test.tsx | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/ExecutionInspector.tsx -->
| `frontend/src/chat/ExecutionInspector.tsx` | Web frontend | Renders persisted RAG, provider and tool attempts without inventing cards for null executions. | ExecutionInspector | react; ../shared/api; ../types | UI/rendering | frontend/src/chat/ExecutionInspector.test.tsx | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/chat/useConversation.ts -->
| `frontend/src/chat/useConversation.ts` | Web frontend | Owns chat request submission, durable run recovery, SSE event reduction and message state transitions. | useConversation | react; ../shared/api; ../chat-contract; ../types | none | frontend/src/chat/useConversation.test.tsx; frontend/src/chat-contract.test.ts | current | Web public; no Core secrets |

## Frontend settings (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/settings/SettingsWorkspace.tsx -->
| `frontend/src/settings/SettingsWorkspace.tsx` | Web frontend | Presents provider, model, appearance, voice and storage settings through the controlled settings bridge. | SettingsWorkspace | react; ../shared/api; ../shared/Field; ../types; ../ui/styledConfirm; ../ui/avatar; ./Modal | UI/rendering | frontend/src/api.test.ts; desktop/settings-bridge.test.cjs | current | Web public; no Core secrets |

## Frontend shell (32)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/index.html -->
| `frontend/index.html` | Web frontend | <!doctype html> | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/public/archive/interactions.css -->
| `frontend/public/archive/interactions.css` | Web frontend | @keyframes mindspace-card-lift { | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/public/archive/manifest.json -->
| `frontend/public/archive/manifest.json` | Web frontend | Defines governed Frontend shell data with top-level keys: schema_version; library_id; license; approval_status; defaults; categories; packs; preview_index. | schema_version; library_id; license; approval_status; defaults; categories; packs; preview_index; assets | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/public/archive/previews/index.json -->
| `frontend/public/archive/previews/index.json` | Web frontend | Defines governed Frontend shell data with top-level keys: schema_version; approval_status; items; expanded_library. | schema_version; approval_status; items; expanded_library | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/public/archive/previews/review.html -->
| `frontend/public/archive/previews/review.html` | Web frontend | <!doctype html> | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/public/pcm-worklet.js -->
| `frontend/public/pcm-worklet.js` | Web frontend | class MindspacePCMProcessor extends AudioWorkletProcessor { | none | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/App.tsx -->
| `frontend/src/App.tsx` | Web frontend | Defines shouldSynthesizeStreamEvent; shouldFollowConversationScroll for the Frontend shell domain. | shouldSynthesizeStreamEvent; shouldFollowConversationScroll | react; ./app/index; ./features/voice; ./features/profile; ./features/memory; ./features/knowledge; ./shared/formatters; ./features/characters | UI/rendering | desktop/app-paths.test.cjs; frontend/src/App.test.tsx; tests/test_application_foundation.py | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/AppProviders.tsx -->
| `frontend/src/app/AppProviders.tsx` | Web frontend | Defines AppProviders for the Frontend shell domain. | AppProviders | react | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/AppShell.tsx -->
| `frontend/src/app/AppShell.tsx` | Web frontend | Defines AppShell for the Frontend shell domain. | AppShell | react | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/index.ts -->
| `frontend/src/app/index.ts` | Web frontend | export { useApplicationData } from "./useApplicationData"; | none | ./useApplicationData; ./useAppNavigation; ./useModalCoordinator | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/useApplicationData.ts -->
| `frontend/src/app/useApplicationData.ts` | Web frontend | Defines useApplicationData for the Frontend shell domain. | useApplicationData | react; ../shared/api; ../features/characters; ../features/settings; ../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/useAppNavigation.ts -->
| `frontend/src/app/useAppNavigation.ts` | Web frontend | Defines useAppNavigation for the Frontend shell domain. | useAppNavigation | react; ../features/characters; ./viewState | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/useModalCoordinator.ts -->
| `frontend/src/app/useModalCoordinator.ts` | Web frontend | Defines useModalCoordinator for the Frontend shell domain. | useModalCoordinator | react; ../features/characters; ../ui/styledConfirm; ./viewState | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/app/viewState.ts -->
| `frontend/src/app/viewState.ts` | Web frontend | Defines ModalName; appViewFromHash for the Frontend shell domain. | ModalName; appViewFromHash | ../features/characters | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/css-governance.allowlist.json -->
| `frontend/src/css-governance.allowlist.json` | Web frontend | Defines governed Frontend shell data with top-level keys: version; override_groups. | version; override_groups | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/scenes/index.ts -->
| `frontend/src/features/scenes/index.ts` | Web frontend | export { ScenePickerPage, sceneAssetPath } from "../../SceneExperience"; | none | ../../SceneExperience; ./useConversationScene | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/scenes/useConversationScene.ts -->
| `frontend/src/features/scenes/useConversationScene.ts` | Web frontend | Defines useConversationScene for the Frontend shell domain. | useConversationScene | react; ../../shared/api; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/main.tsx -->
| `frontend/src/main.tsx` | Web frontend | Maintains Frontend shell configuration or execution behavior. | none | react-dom/client; ./app/AppProviders; ./app/AppRouter; ./app/AppShell | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/redesign.overrides.css -->
| `frontend/src/redesign.overrides.css` | Web frontend | MINDSPACE PRODUCT OVERRIDE AUTHORITY | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/SceneExperience.tsx -->
| `frontend/src/SceneExperience.tsx` | Web frontend | Defines sceneAssetPath; ScenePickerPage for the Frontend shell domain. | sceneAssetPath; ScenePickerPage | react; ./shared/api; ./types | UI/rendering | frontend/src/SceneExperience.test.tsx | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/shared/Field.tsx -->
| `frontend/src/shared/Field.tsx` | Web frontend | Defines Field for the Frontend shell domain. | Field | ./formatters | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/shared/formatters.ts -->
| `frontend/src/shared/formatters.ts` | Web frontend | export const asRecord = (value: unknown): Record<string, unknown> => | asRecord; bool; num; str; friendlyValue; formatTime | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/shared/Modal.tsx -->
| `frontend/src/shared/Modal.tsx` | Web frontend | Defines Modal for the Frontend shell domain. | Modal | react | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/shared/turn.ts -->
| `frontend/src/shared/turn.ts` | Web frontend | Defines TurnSend for the Frontend shell domain. | TurnSend | ../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/speech.ts -->
| `frontend/src/speech.ts` | Web frontend | const SENTENCE_BOUNDARY = /(?:[。！？!?；;]+\|…{2,}\|\.{3,}\|\n+)/g; | normalizeSpeechSegment; hasSpeakableContent; stripLeadingTtsFiller; SpeechSegmenter; segmentSpeechText; estimateDeliveredPrefix | none | none | frontend/src/speech.test.ts | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/styles.css -->
| `frontend/src/styles.css` | Web frontend | Defines the Frontend shell visual stylesheet and responsive presentation rules. | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/types.ts -->
| `frontend/src/types.ts` | Web frontend | export type Role = "user" \| "assistant"; | Role; InteractionTag; ChatAttachment; Message; ToolExecution; PresentationModeResolved; InitiativeTrigger; VoiceInteractionMode; VoiceInteractionContext; SessionSummary | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/ui/avatar.tsx -->
| `frontend/src/ui/avatar.tsx` | Web frontend | Defines DEFAULT_AVATARS; normalizeAvatarConfig; avatarStyle; PortraitAvatar for the Frontend shell domain. | DEFAULT_AVATARS; normalizeAvatarConfig; avatarStyle; PortraitAvatar | react; ../types | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/ui/styledConfirm.ts -->
| `frontend/src/ui/styledConfirm.ts` | Web frontend | export interface ConfirmationOptions { | ConfirmationOptions; styledConfirm | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/vite-env.d.ts -->
| `frontend/src/vite-env.d.ts` | Web frontend | <reference types="vite/client" /> | none | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/tsconfig.json -->
| `frontend/tsconfig.json` | Web frontend | Defines governed Frontend shell data with top-level keys: compilerOptions; include; references. | compilerOptions; include; references | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/vite.config.ts -->
| `frontend/vite.config.ts` | Web frontend | Maintains Frontend shell configuration or execution behavior. | none | vite; @vitejs/plugin-react; node:path; node:fs | filesystem; environment | repository policy / full suite | current | Web public; no Core secrets |

## Frontend transport (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/api.ts -->
| `frontend/src/api.ts` | Web frontend | Provides the typed API/SSE client, durable run stream access and desktop-settings transport switch. | apiV1Request; openChatStream; openRunEventStream; cancelRunRequest | ./shared/api | none | frontend/src/api.test.ts | current | Web public; no Core secrets |

## Legacy compatibility (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:src/mindspace_graph/api_routes/legacy_routes.py -->
| `src/mindspace_graph/api_routes/legacy_routes.py` | Core backend | Keeps explicit legacy and 410 route contracts isolated from current product route modules. | register_routes | __future__; fastapi; context | none | tests/test_api_route_contract.py | current | Core protected release surface |

## Memory and retrieval (26)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:docs/DEVELOPER_MEMORY_RAG_PROMPT.md -->
| `docs/DEVELOPER_MEMORY_RAG_PROMPT.md` | Documentation | Documents “Mindspace 0.4.5 记忆、召回、上下文与 Prompt 开发者手册” with historical authority. | Mindspace 0.4.5 记忆、召回、上下文与 Prompt 开发者手册 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/structured-json-memory.md -->
| `docs/structured-json-memory.md` | Documentation | Documents “JSON 字段标签记忆” with prototype authority. | JSON 字段标签记忆 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:frontend/src/features/knowledge/index.ts -->
| `frontend/src/features/knowledge/index.ts` | Web frontend | export { KnowledgeDialog } from "./KnowledgeDialog"; | none | ./KnowledgeDialog | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/knowledge/KnowledgeDialog.tsx -->
| `frontend/src/features/knowledge/KnowledgeDialog.tsx` | Web frontend | Defines KnowledgeDialog for the Memory and retrieval domain. | KnowledgeDialog | react; ../../shared/Modal; ../../shared/Field; ../../shared/api; ../../shared/formatters; ../../types; ../../ui/styledConfirm | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/memory/index.ts -->
| `frontend/src/features/memory/index.ts` | Web frontend | export { MemoryDialog } from "./MemoryDialog"; | none | ./MemoryDialog | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/memory/MemoryDialog.tsx -->
| `frontend/src/features/memory/MemoryDialog.tsx` | Web frontend | Defines MemoryDialog for the Memory and retrieval domain. | MemoryDialog | react; ../../shared/Modal; ../../shared/api; ../../shared/formatters; ../../types; ../../ui/styledConfirm | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/adapters/in_memory.py -->
| `src/mindspace_graph/adapters/in_memory.py` | Core backend | Deterministic adapters used by tests and the zero-configuration demo. | InMemoryRetriever; InMemoryProfileRepository; InMemorySessionRepository; DeterministicLanguageModel; RegexRolePolicy; InMemoryAudit; demo_dependencies | __future__; json; re; collections.abc; copy; dataclasses; datetime; typing | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/local_retriever.py -->
| `src/mindspace_graph/adapters/local_retriever.py` | Core backend | Small persistent lexical retriever used before a vector backend is configured. | LocalKnowledgeRetriever | __future__; hashlib; json; math; re; collections; datetime; pathlib | filesystem | tests/test_local_retriever.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/adapters/structured_memory.py -->
| `src/mindspace_graph/adapters/structured_memory.py` | Core backend | Deterministic JSON-tagged memory without model-authored classifications. | StructuredMemoryStore | __future__; hashlib; json; re; copy; datetime; pathlib; threading | filesystem; database/state | tests/test_structured_memory.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/api_routes/memory_knowledge.py -->
| `src/mindspace_graph/api_routes/memory_knowledge.py` | Core backend | Registers memory, knowledge, entity and retrieval maintenance endpoints against the shared API context. | register_routes | __future__; json; typing; fastapi; pydantic; mindspace_graph.event_memory; mindspace_graph.memory_registry; context | database/state | tests/test_api.py; tests/test_memory_registry.py; tests/test_structured_memory.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/application/retrieval_warmup.py -->
| `src/mindspace_graph/application/retrieval_warmup.py` | Core backend | Background retrieval-index warmup coordination for committed chat turns. | RetrievalWarmupCoordinator | __future__; asyncio; time; mindspace_graph.models; mindspace_graph.ports | none | tests/test_retrieval_warmup.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/compaction.py -->
| `src/mindspace_graph/compaction.py` | Core backend | Low-priority context compaction kept outside the conversational graph. | COMPACTION_SYSTEM_PROMPT; build_compaction_messages; parse_compaction_output; ContextCompactionService | __future__; asyncio; json; re; collections.abc; typing; mindspace_graph.context_ledger; mindspace_graph.models | database/state | tests/test_context_compaction.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/event_memory.py -->
| `src/mindspace_graph/event_memory.py` | Core backend | Small, mutable event memory between live context and long-term retrieval. | PENDING_LIMIT; SUBJECT_CATEGORIES; should_consider_event; event_memory_lane; parse_event_operation; build_event_extraction_messages; resolve_event_target; normalize_event_operation; EventMemoryStore; EventMemoryWritebackService | __future__; asyncio; copy; datetime; json; re; threading; typing | database/state | tests/test_event_memory_tool_conflict.py; tests/test_event_memory.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/memory_registry.py -->
| `src/mindspace_graph/memory_registry.py` | Core backend | Single source of truth for persistent profile fields and memory reduction. | MemoryField; FIELDS; MemoryRegistry; V2_FIELDS; DEFAULT_MEMORY_REGISTRY | __future__; dataclasses; typing | none | tests/test_memory_registry.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/memory_service.py -->
| `src/mindspace_graph/memory_service.py` | Core backend | Application service for user-visible structured memory operations. | StructuredMemoryService | __future__; json; re; contextlib; copy; threading; typing; uuid | database/state | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/memory_update.py -->
| `src/mindspace_graph/memory_update.py` | Core backend | Conditional, evidence-bound extraction of conversational profile changes. | should_extract_memory; build_memory_extraction_messages; parse_memory_plan | __future__; json; re; typing; mindspace_graph.memory_registry; mindspace_graph.models | none | tests/test_memory_update.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/memory_writeback.py -->
| `src/mindspace_graph/memory_writeback.py` | Core backend | Evidence-bound, post-turn profile memory writeback. | MemoryWritebackService | __future__; asyncio; collections.abc; typing; mindspace_graph.memory_update; mindspace_graph.event_memory; mindspace_graph.models; mindspace_graph.policies | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/retrieval_fusion.py -->
| `src/mindspace_graph/retrieval_fusion.py` | Core backend | Deterministic hybrid retrieval primitives. | tokenize; BM25Plus; reciprocal_rank_fusion; bounded_boost; CrossEncoderReranker | __future__; math; re; collections; collections.abc; dataclasses; typing; mindspace_graph.models | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:tests/test_context_compaction.py -->
| `tests/test_context_compaction.py` | Tests | Verifies test_compaction_excludes_decorative_stage_prose_from_continuity; test_background_compaction_activates_a_new_epoch_and_keeps_recent_tail; test_semantic_compaction_starts_at_round_fifteen_and_retains_three_raw_rounds; test_compaction_sanitizes_adult_details_even_if_model_mislabels_the_lane; test_adult_facts_are_retained_only_in_the_adult_prompt_view. | profiles; test_compaction_excludes_decorative_stage_prose_from_continuity; request; append_round; test_background_compaction_activates_a_new_epoch_and_keeps_recent_tail; test_semantic_compaction_starts_at_round_fifteen_and_retains_three_raw_rounds; test_compaction_sanitizes_adult_details_even_if_model_mislabels_the_lane; test_adult_facts_are_retained_only_in_the_adult_prompt_view; test_compaction_rejects_assistant_only_shared_memory_claims; test_compaction_yields_to_active_conversation_and_runs_afterward | __future__; asyncio; json; copy; mindspace_graph.adapters.file_storage; mindspace_graph.adapters.in_memory; mindspace_graph.compaction; mindspace_graph.context_ledger | database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_event_memory_tool_conflict.py -->
| `tests/test_event_memory_tool_conflict.py` | Tests | Verifies test_web_turn_replaces_false_event_memory_saved_claim; test_web_turn_keeps_truthful_not_saved_statement. | test_web_turn_replaces_false_event_memory_saved_claim; test_web_turn_keeps_truthful_not_saved_statement | mindspace_graph.tool_chain | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_event_memory.py -->
| `tests/test_event_memory.py` | Tests | Verifies test_event_memory_enforces_three_plus_three; test_subject_slot_replaces_only_its_category; test_parser_and_candidate_gate_are_bounded; test_destructive_target_is_corrected_by_current_user_wording; test_event_slot_and_lifecycle_are_normalized_from_user_evidence. | operation; test_event_memory_enforces_three_plus_three; test_subject_slot_replaces_only_its_category; test_parser_and_candidate_gate_are_bounded; test_destructive_target_is_corrected_by_current_user_wording; test_event_slot_and_lifecycle_are_normalized_from_user_evidence | mindspace_graph.event_memory; mindspace_graph.product_database | database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_local_retriever.py -->
| `tests/test_local_retriever.py` | Tests | Verifies test_parent_child_chunking_expands_retrieved_context; test_stored_vectors_are_not_recomputed_and_knowledge_file_is_cached; test_chat_retrieval_reuses_history_loaded_by_graph. | test_parent_child_chunking_expands_retrieved_context; RecordingEmbeddingModel; test_stored_vectors_are_not_recomputed_and_knowledge_file_is_cached; test_chat_retrieval_reuses_history_loaded_by_graph | __future__; json; numpy; mindspace_graph.adapters.file_storage; mindspace_graph.adapters.local_retriever | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_memory_registry.py -->
| `tests/test_memory_registry.py` | Tests | Verifies test_registry_has_unique_codes_locations_and_complete_business_metadata; test_read_json_pointer_returns_none_when_an_intermediate_node_is_missing; test_memory_center_update_delete_and_restore_keep_supported_profile_and_index_aligned. | test_registry_has_unique_codes_locations_and_complete_business_metadata; test_read_json_pointer_returns_none_when_an_intermediate_node_is_missing; test_memory_center_update_delete_and_restore_keep_supported_profile_and_index_aligned | __future__; mindspace_graph.adapters.file_storage; mindspace_graph.adapters.structured_memory; mindspace_graph.infrastructure.storage.json_patch; mindspace_graph.memory_registry; mindspace_graph.memory_service; mindspace_graph.models | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_memory_update.py -->
| `tests/test_memory_update.py` | Tests | Verifies test_memory_extraction_gate_skips_acknowledgements_and_time_questions; test_memory_extraction_gate_accepts_user_preferences_and_agent_self_questions; test_memory_plan_parser_accepts_fenced_json; test_memory_plan_parser_recovers_unique_target_and_current_evidence. | test_memory_extraction_gate_skips_acknowledgements_and_time_questions; test_memory_extraction_gate_accepts_user_preferences_and_agent_self_questions; test_memory_plan_parser_accepts_fenced_json; test_memory_plan_parser_recovers_unique_target_and_current_evidence | mindspace_graph.memory_update | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_retrieval_warmup.py -->
| `tests/test_retrieval_warmup.py` | Tests | Verifies test_first_fifteen_rounds_index_only_then_round_sixteen_enables_rag; test_client_cannot_force_cold_retrieval_ready; test_rank_context_excludes_automatic_knowledge_and_enforces_chat_memory_quotas. | test_first_fifteen_rounds_index_only_then_round_sixteen_enables_rag; test_client_cannot_force_cold_retrieval_ready; test_rank_context_excludes_automatic_knowledge_and_enforces_chat_memory_quotas | __future__; asyncio; mindspace_graph.models; mindspace_graph.nodes; mindspace_graph.service; mindspace_graph.settings | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_structured_memory.py -->
| `tests/test_structured_memory.py` | Tests | Verifies test_untagged_pool_is_deduplicated_and_strictly_bounded; test_one_committed_json_tag_is_immediately_active_and_keeps_original_text; test_active_limits_are_isolated_by_character_and_legacy_records_remain_readable; test_targeted_rebuild_replaces_only_one_character_and_uses_distinct_episodes; test_opposing_preference_reuses_one_slot_and_multiple_tags_share_one_episode. | test_untagged_pool_is_deduplicated_and_strictly_bounded; test_one_committed_json_tag_is_immediately_active_and_keeps_original_text; test_active_limits_are_isolated_by_character_and_legacy_records_remain_readable; test_targeted_rebuild_replaces_only_one_character_and_uses_distinct_episodes; test_opposing_preference_reuses_one_slot_and_multiple_tags_share_one_episode; test_temporal_memory_expires_to_a_tombstone_while_persistent_memory_remains; test_fair_ranking_reserves_a_slot_for_an_underexposed_memory; test_family_limit_delays_repeated_high_weight_slots_until_diverse_fields_are_seen | __future__; json; copy; datetime; types; mindspace_graph.adapters.structured_memory; mindspace_graph.memory_registry; mindspace_graph.memory_service | none | direct test file | current | Development-only; not runtime payload by default |

## Native tools (7)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:docs/READ_ONLY_CAPABILITIES.md -->
| `docs/READ_ONLY_CAPABILITIES.md` | Documentation | Documents “0.9.0 原生工具能力” with current authority. | 0.9.0 原生工具能力 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:scripts/run_082_two_card_tool_benchmark.py -->
| `scripts/run_082_two_card_tool_benchmark.py` | Developer tooling | Deprecated 0.8.2 benchmark; retained as a fail-closed tombstone. | none | none | database/state | repository policy / full suite | deprecated | Developer tool; release only when allowlisted |
<!-- INDEXED:src/mindspace_graph/capabilities.py -->
| `src/mindspace_graph/capabilities.py` | Core backend | Bounded, read-only capabilities used by the conversational graph. | DEFAULT_CAPABILITY_SETTINGS; ReadOnlyCapabilityService | __future__; ipaddress; re; socket; collections.abc; concurrent.futures; datetime; html | network | tests/test_capabilities.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/native_tools.py -->
| `src/mindspace_graph/native_tools.py` | Core backend | Defines the compact native tool schemas, provider eligibility and validated host-call conversion. | NATIVE_TOOL_GUIDANCE; native_tool_definitions; native_tool_choice; native_call_to_instruction; supports_native_tools | __future__; json; re; typing; uuid; tool_chain | none | tests/test_native_tools.py; tests/test_capabilities.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/tool_chain.py -->
| `src/mindspace_graph/tool_chain.py` | Core backend | Validated host-side tool instructions and execution results. | FINAL_AFTER_TOOL_PROTOCOL; ToolInstruction; ToolExecutionResult; validate_task_command; task_review_messages; parse_task_review; tool_result_json; failed_result; execute_memory_tool; enforce_tool_claims | __future__; hashlib; json; re; time; datetime; typing; urllib.parse | none | repository policy / full suite | current | Core protected release surface |
<!-- INDEXED:tests/test_capabilities.py -->
| `tests/test_capabilities.py` | Tests | Verifies test_result_json_escapes_external_markup; test_route_hint_is_zero_call_and_local_is_never_exposed; test_web_executor_uses_public_get_and_returns_bounded_sources; test_normal_chat_uses_one_model_call; test_l3_memory_uses_two_calls_and_one_execution. | capability_config; test_result_json_escapes_external_markup; test_route_hint_is_zero_call_and_local_is_never_exposed; web_transport; test_web_executor_uses_public_get_and_returns_bounded_sources; ScriptedModel; native_call; FakeCharacters; invoke_with_model; test_normal_chat_uses_one_model_call | __future__; httpx; mindspace_graph.adapters.in_memory; mindspace_graph.capabilities; mindspace_graph.graph; mindspace_graph.models; mindspace_graph.tool_chain | network | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_native_tools.py -->
| `tests/test_native_tools.py` | Tests | Verifies test_hinted_native_tool_sets_are_small_and_required; test_native_calls_map_to_validated_host_instructions; test_native_task_discards_model_invented_clock_time; test_native_tools_only_enable_for_official_deepseek_endpoint. | test_hinted_native_tool_sets_are_small_and_required; test_native_calls_map_to_validated_host_instructions; test_native_task_discards_model_invented_clock_time; test_native_tools_only_enable_for_official_deepseek_endpoint | json; mindspace_graph.native_tools | none | direct test file | current | Development-only; not runtime payload by default |

## Provider adapter (1)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:src/mindspace_graph/adapters/openai_compatible.py -->
| `src/mindspace_graph/adapters/openai_compatible.py` | Core backend | Implements OpenAI-compatible streaming, structured output compatibility ladders, native tool calls and provider attempt diagnostics. | EmptyVisibleContentError; OpenAICompatibleLanguageModel | __future__; json; collections.abc; threading; time; typing; httpx; mindspace_graph.models | network | tests/test_openai_compatible.py | current | Core protected release surface |

## Repository governance (38)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:.github/workflows/ci.yml -->
| `.github/workflows/ci.yml` | Governance/config | name: 0.9.0 quality gates | none | none | database/state | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:config/.env.example -->
| `config/.env.example` | Governance/config | Maintains Repository governance configuration or execution behavior. | none | none | none | repository policy / full suite | current | Development/config; never contains secrets |
<!-- INDEXED:config/codebase-index-overrides.json -->
| `config/codebase-index-overrides.json` | Governance/config | Defines governed Repository governance data with top-level keys: src/mindspace_graph/api.py; src/mindspace_graph/api_routes/context.py; src/mindspace_graph/api_routes/chat_runs.py; src/mindspace_graph/api_routes/characters_cards.py; src/mindspace_graph/api_routes/destiny_routes.py; src/mindspace_graph/api_routes/system_settings.py; src/mindspace_graph/api_routes/memory_knowledge.py; src/mindspace_graph/api_routes/audio_scenes.py. | src/mindspace_graph/api.py; src/mindspace_graph/api_routes/context.py; src/mindspace_graph/api_routes/chat_runs.py; src/mindspace_graph/api_routes/characters_cards.py; src/mindspace_graph/api_routes/destiny_routes.py; src/mindspace_graph/api_routes/system_settings.py; src/mindspace_graph/api_routes/memory_knowledge.py; src/mindspace_graph/api_routes/audio_scenes.py; src/mindspace_graph/api_routes/legacy_routes.py; src/mindspace_graph/conversation_runs.py | none | database/state | repository policy / full suite | current | Development/config; never contains secrets |
<!-- INDEXED:config/service-ports.json -->
| `config/service-ports.json` | Governance/config | Defines governed Repository governance data with top-level keys: schema_version; host; services; environment. | schema_version; host; services; environment | none | none | desktop/service-ports.test.cjs | current | Release/config contract; never contains secrets |
<!-- INDEXED:scripts/bootstrap_acceptance.py -->
| `scripts/bootstrap_acceptance.py` | Developer tooling | Run an isolated four-turn real-LLM profile bootstrap acceptance test. | ROOT; USER_PERSONA; SYSTEM_PROMPT; MESSAGES; main | __future__; argparse; asyncio; json; sys; datetime; pathlib; typing | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/build_art_library.py -->
| `scripts/build_art_library.py` | Developer tooling | Build the Mindspace 0.7 art library and its signed-data-ready manifest. | ROOT; PUBLIC_ROOT; ARCHIVE_ROOT; RAW_ROOT; MANIFEST_PATH; TERRACOTTA; TERRACOTTA_DARK; JADE; JADE_DARK; PARCHMENT | __future__; hashlib; json; math; random; re; pathlib; typing | filesystem; database/state | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/build.ps1 -->
| `scripts/build.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/cleanup-legacy-mindspace.ps1 -->
| `scripts/cleanup-legacy-mindspace.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/codebase-index-python.py -->
| `scripts/codebase-index-python.py` | Developer tooling | Extract Python module metadata for the generated codebase index. | inspect_file; main | __future__; ast; json; sys; pathlib | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/deploy-installer.py -->
| `scripts/deploy-installer.py` | Developer tooling | Inspect a release host and atomically upload a signed Mindspace installer. | fingerprint; connect; run; inspect; upload; promote_staged_file; read_remote; site_info; has_authenticode_signature; build_download_manifest | __future__; argparse; base64; hashlib; json; os; posixpath; re | filesystem; environment | tests/test_deploy_installer.py | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/generate-codebase-index.mjs -->
| `scripts/generate-codebase-index.mjs` | Developer tooling | Builds architecture and per-file indexes from the explicit maintained-file set and fails check mode on coverage drift. | CLI: generate or --check | node:child_process; node:fs; node:path; node:url; )) return comment;   const symbols = compact(py?.exports ?? js.exports, 5);   if (symbols !== | filesystem; database/state; network; Electron IPC; process execution; environment | node scripts/generate-codebase-index.mjs --check | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/portable-start.ps1 -->
| `scripts/portable-start.ps1` | Developer tooling | [CmdletBinding()] | none | none | process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/prepare-gpt-sovits.ps1 -->
| `scripts/prepare-gpt-sovits.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/promote-050-server.sh -->
| `scripts/promote-050-server.sh` | Developer tooling | usr/bin/env bash | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/readme-demo-server.mjs -->
| `scripts/readme-demo-server.mjs` | Developer tooling | Maintains Repository governance configuration or execution behavior. | none | node:http | network; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/render-readme-images.py -->
| `scripts/render-readme-images.py` | Developer tooling | Defines WIDTH; HEIGHT; HERO_HEIGHT; INK; MUTED for the Repository governance domain. | WIDTH; HEIGHT; HERO_HEIGHT; INK; MUTED; ACCENT; JADE; font; gradient; rounded | __future__; argparse; pathlib; PIL | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/repair.ps1 -->
| `scripts/repair.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/romance_acceptance.py -->
| `scripts/romance_acceptance.py` | Developer tooling | Run a 50-turn isolated romance-oriented acceptance conversation. | ROOT; USER_PERSONA; SYSTEM_PROMPT; DIALOGUE; main | __future__; argparse; asyncio; json; sys; datetime; pathlib; typing | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_deepseek_desktop_r18_90.py -->
| `scripts/run_deepseek_desktop_r18_90.py` | Developer tooling | Private 90-turn DeepSeek regression from the latest desktop R18 user inputs. | ROOT; SESSION_PATH; CHARACTER_PATH; TURN_COUNT; text; text_items; role_state; digest; main | __future__; hashlib; json; os; statistics; sys; time; datetime | filesystem; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_gemma_sample_inputs.py -->
| `scripts/run_gemma_sample_inputs.py` | Developer tooling | Run two real Gemma roleplay trials using only Q lines from a local quality sample. | ROOT; SAMPLE_PATH; DESKTOP_SETTINGS; REPORTS; MODEL; BASE_URL; RUNS; sample_questions; desktop_api_key; call_gemma | __future__; json; os; re; statistics; sys; time; datetime | filesystem; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_gemma_style_fix_10.py -->
| `scripts/run_gemma_style_fix_10.py` | Developer tooling | One live Gemma regression after the dialogue-first role prompt change. | ROOT; main | __future__; json; re; sys; time; datetime; pathlib; mindspace_graph.role_runtime | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_gemma_twice_20.py -->
| `scripts/run_gemma_twice_20.py` | Developer tooling | Run two identical 20-turn Gemma chats through the real Mindspace API. | ROOT; BASE_URL; KEY; JSON_REPORT; MD_REPORT; TURNS; now; api; percentile; card | __future__; json; math; os; statistics; sys; time; urllib.error | environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/run_roleplay_state_machine_benchmark.py -->
| `scripts/run_roleplay_state_machine_benchmark.py` | Developer tooling | Run the same black-box chat workflow against two real model providers. | ROOT; BASE_URL; REPORT_PATH; MODEL_SOURCES; now; request; candidate_configs; provider_config; configure_isolated_provider; card | __future__; hashlib; json; math; os; sys; time; urllib.error | environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/seal-r18-library.mjs -->
| `scripts/seal-r18-library.mjs` | Developer tooling | Maintains Repository governance configuration or execution behavior. | none | node:crypto; node:fs; node:path | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/service-ports.ps1 -->
| `scripts/service-ports.ps1` | Developer tooling | Set-StrictMode -Version Latest | none | none | environment | desktop/service-ports.test.cjs | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/smoke-qwen3-base.py -->
| `scripts/smoke-qwen3-base.py` | Developer tooling | Exercise Mindspace's Qwen3 Base provider and write playable WAV artifacts. | SAMPLES; parse_args; main | __future__; argparse; asyncio; json; pathlib; time; wave; mindspace_graph.audio | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/start.ps1 -->
| `scripts/start.ps1` | Developer tooling | [CmdletBinding()] | none | none | process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/stop-services.ps1 -->
| `scripts/stop-services.ps1` | Developer tooling | [CmdletBinding(SupportsShouldProcess)] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/sync-gpt-sovits-catalog.mjs -->
| `scripts/sync-gpt-sovits-catalog.mjs` | Developer tooling | Maintains Repository governance configuration or execution behavior. | none | node:fs; node:path; node:url | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/sync-models.ps1 -->
| `scripts/sync-models.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/today_81_acceptance.py -->
| `scripts/today_81_acceptance.py` | Developer tooling | Replay the current 81-round desktop conversation in an isolated workspace. | ROOT; SOURCE_SESSION_ID; SOURCE_CHARACTER_ID; main | __future__; argparse; asyncio; json; shutil; sqlite3; sys; collections | filesystem; database/state | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-cjs-syntax.mjs -->
| `scripts/verify-cjs-syntax.mjs` | Developer tooling | Maintains Repository governance configuration or execution behavior. | none | node:child_process; node:fs; node:path; node:url | process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-current-doc-paths.mjs -->
| `scripts/verify-current-doc-paths.mjs` | Developer tooling | Checks path-like references in current authority documents and fails when a maintained target moved or disappeared. | CLI verification | node:fs; node:path; node:url | filesystem | CI governance job; repository policy | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-frontend-boundaries.mjs -->
| `scripts/verify-frontend-boundaries.mjs` | Developer tooling | usr/bin/env node | none | node:fs; node:path; node:url | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-powershell-syntax.ps1 -->
| `scripts/verify-powershell-syntax.ps1` | Developer tooling | $ErrorActionPreference = "Stop" | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-repository-policy.mjs -->
| `scripts/verify-repository-policy.mjs` | Developer tooling | Enforces dependency, secret, source-map, documentation, release allowlist, bootstrap and index-completeness policy. | CLI verification | node:child_process; node:fs; node:path; node:url | filesystem; database/state; process execution | CI governance job | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-source-integrity.ps1 -->
| `scripts/verify-source-integrity.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify.ps1 -->
| `scripts/verify.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |

## Settings and provider (12)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:desktop/secret-store.cjs -->
| `desktop/secret-store.cjs` | Desktop Launcher | const fs = require("node:fs"); | module.exports | node:fs; node:path | filesystem | repository policy / full suite | current | Launcher public code; OS-encrypted secret boundary |
<!-- INDEXED:desktop/settings-bridge.test.cjs -->
| `desktop/settings-bridge.test.cjs` | Desktop Launcher | Verifies desktop save applies Core first, encrypts secrets, and survives store restart; Core failure leaves the prior encrypted secret unchanged; secure-store failure reports a retryable partial state without replacing the old secret; explicit clear is distinct from an omitted or empty secret; desktop GET reports secure persistence immediately after save. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./secret-store.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:frontend/src/features/settings/DiagnosticsDialogView.tsx -->
| `frontend/src/features/settings/DiagnosticsDialogView.tsx` | Web frontend | Defines DiagnosticsDialog for the Settings and provider domain. | DiagnosticsDialog | react; ../../shared/api; ../../shared/formatters; ../../shared/Modal; ../../types; ../../ui/styledConfirm | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/settings/index.ts -->
| `frontend/src/features/settings/index.ts` | Web frontend | export { Modal } from "../../shared/Modal"; | none | ../../shared/Modal; ../../shared/Field; ../../settings/SettingsWorkspace; ./DiagnosticsDialogView; ./useModelSelection; ./useSettingsSynchronization; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/settings/useModelSelection.ts -->
| `frontend/src/features/settings/useModelSelection.ts` | Web frontend | Defines useModelSelection for the Settings and provider domain. | useModelSelection | react; ../../shared/api; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/settings/useSettingsSynchronization.ts -->
| `frontend/src/features/settings/useSettingsSynchronization.ts` | Web frontend | Defines useSettingsSynchronization for the Settings and provider domain. | useSettingsSynchronization | react; ../../shared/api; ../../types | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/settings/Modal.tsx -->
| `frontend/src/settings/Modal.tsx` | Web frontend | Compatibility entrypoint for settings code that has not moved to shared UI yet. | none | ../shared/Modal | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/api_routes/system_settings.py -->
| `src/mindspace_graph/api_routes/system_settings.py` | Core backend | Registers product settings, provider connection tests, model discovery and public configuration endpoints. | register_routes | __future__; typing; uuid; httpx; fastapi; fastapi.responses; context | database/state; network | tests/test_api.py; tests/test_product_config_secrets.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/product_config.py -->
| `src/mindspace_graph/product_config.py` | Core backend | Runtime-editable product configuration with redacted public snapshots. | ProductConfigStore | __future__; json; copy; pathlib; threading; typing; mindspace_graph.infrastructure.storage.json_io; mindspace_graph.capabilities | filesystem | tests/test_product_config_secrets.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/settings.py -->
| `src/mindspace_graph/settings.py` | Core backend | Environment-driven product settings with safe local defaults. | AppSettings | __future__; os; dataclasses; pathlib | filesystem; environment | desktop/settings-bridge.test.cjs; tests/test_settings.py | current | Core protected release surface |
<!-- INDEXED:tests/test_product_config_secrets.py -->
| `tests/test_product_config_secrets.py` | Tests | Verifies test_environment_secrets_survive_public_save_without_persistence; test_legacy_plaintext_secrets_are_used_once_and_atomically_scrubbed; test_launcher_secret_wins_over_legacy_plaintext_while_disk_is_scrubbed; test_runtime_secret_patch_is_process_only_and_public_fields_persist; test_atomic_public_save_failure_preserves_disk_and_runtime_state. | test_environment_secrets_survive_public_save_without_persistence; test_legacy_plaintext_secrets_are_used_once_and_atomically_scrubbed; test_launcher_secret_wins_over_legacy_plaintext_while_disk_is_scrubbed; test_runtime_secret_patch_is_process_only_and_public_fields_persist; test_atomic_public_save_failure_preserves_disk_and_runtime_state; test_settings_get_and_patch_never_expose_or_persist_secrets; test_launcher_environment_secret_reaches_model_adapter_without_disk_copy | __future__; json; pytest; fastapi.testclient; mindspace_graph.product_config; mindspace_graph.api; mindspace_graph.models; mindspace_graph.settings | filesystem | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_settings.py -->
| `tests/test_settings.py` | Tests | Verifies test_home_only_core_start_reopens_the_existing_home_data; test_explicit_runtime_directory_still_has_priority. | test_home_only_core_start_reopens_the_existing_home_data; test_explicit_runtime_directory_still_has_priority | __future__; mindspace_graph.settings | none | direct test file | current | Development-only; not runtime payload by default |

## V7 destiny (8)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:frontend/src/destiny-canvas.css -->
| `frontend/src/destiny-canvas.css` | Web frontend | Defines the V7 destiny visual stylesheet and responsive presentation rules. | none | none | UI/rendering | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/DestinyCanvas.test.tsx -->
| `frontend/src/DestinyCanvas.test.tsx` | Web frontend | Verifies /; runs 8 directions, 96 cards and twelve V7 selections before entering local chat; keeps the persistent uploaded avatar URL while preserving crop adjustments; keeps the V7 resume key when a temporary journey restore request fails; keeps the V7 resume key when the definition endpoint is unavailable. | none | @testing-library/react; @testing-library/user-event; vitest; ./DestinyCanvas | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/DestinyCanvas.tsx -->
| `frontend/src/DestinyCanvas.tsx` | Web frontend | Defines mergeUploadedAvatar; DestinyCanvas; DrawWorkshop for the V7 destiny domain. | mergeUploadedAvatar; DestinyCanvas; DrawWorkshop | react; ./shared/api | UI/rendering | frontend/src/DestinyCanvas.test.tsx | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/features/destiny/index.ts -->
| `frontend/src/features/destiny/index.ts` | Web frontend | export { DrawWorkshop } from "../../DestinyCanvas"; | none | ../../DestinyCanvas | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:src/mindspace_graph/api_routes/destiny_routes.py -->
| `src/mindspace_graph/api_routes/destiny_routes.py` | Core backend | Registers V7 journey, avatar, archetype, 6+6 card batch, selection, synthesis and commit endpoints. | register_routes | __future__; json; pathlib; typing; uuid; fastapi; mindspace_graph.destiny; context | filesystem; database/state | tests/test_destiny.py; tests/test_api_route_contract.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/destiny.py -->
| `src/mindspace_graph/destiny.py` | Core backend | Owns V7 journey state, two half-card batches, tolerant card parsing, selections, V2 synthesis and commit. | DESTINY_SCHEMA_VERSION; JOURNEY_SCHEMA_VERSION; ARCHETYPE_COUNT; DESTINY_SLOTS; DestinySeed; DestinySelectionRequest; public_destiny_definition; DestinyService | __future__; ast; asyncio; json; re; copy; datetime; typing | database/state | tests/test_destiny.py; tests/test_destiny_dialogue_regression.py | current | Core protected release surface |
<!-- INDEXED:tests/test_destiny_dialogue_regression.py -->
| `tests/test_destiny_dialogue_regression.py` | Tests | Verifies test_destiny_character_thirty_round_fuzzy_daily_regression. | CASES; ThirtyRoundDailyModel; test_destiny_character_thirty_round_fuzzy_daily_regression | __future__; asyncio; json; os; re; collections; copy; difflib | filesystem; environment | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_destiny.py -->
| `tests/test_destiny.py` | Tests | Verifies test_destiny_definition_exposes_v7_slots_and_willingness_levels; test_full_destiny_journey_uses_two_card_calls_inside_three_visible_stages; test_failed_first_half_preserves_successful_second_half_and_retries_only_first; test_failed_second_half_preserves_successful_first_half_and_retries_only_second; test_category_name_cannot_be_accepted_as_a_direct_card_label. | make_settings; seed_payload; people_payload; cards_payload; compact_cards_payload; card_payload; test_destiny_definition_exposes_v7_slots_and_willingness_levels; test_full_destiny_journey_uses_two_card_calls_inside_three_visible_stages; test_failed_first_half_preserves_successful_second_half_and_retries_only_first; test_failed_second_half_preserves_successful_first_half_and_retries_only_second | __future__; json; copy; fastapi.testclient; mindspace_graph.api; mindspace_graph.destiny; mindspace_graph.settings | database/state | direct test file | current | Development-only; not runtime payload by default |

## Verification (42)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:desktop/announcement-policy.test.cjs -->
| `desktop/announcement-policy.test.cjs` | Desktop Launcher | Verifies announcement opens once per launcher process only for a newer release; announcement remains eligible while a new update downloads or waits to install. | none | node:assert/strict; node:test; ./announcement-policy.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/app-paths.test.cjs -->
| `desktop/app-paths.test.cjs` | Desktop Launcher | Verifies Mindspace uses one LocalAppData application root; 0.3.4 data and models migrate without copying virtual environments; misrouted ASR final-pass models are adopted without another download. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./app-paths.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/bootstrap-core.test.cjs -->
| `desktop/bootstrap-core.test.cjs` | Desktop Launcher | Verifies packaged launcher uses a writable user workspace instead of the build-machine hint; first launch expands the bundled core into the selected workspace; newer bundled core atomically replaces code so stale files disappear while external data stays unchanged; failed post-switch validation restores the previous Core; backup cleanup failure blocks startup without attempting a destructive rollback. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./bootstrap-core.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/companion-assets.test.cjs -->
| `desktop/companion-assets.test.cjs` | Desktop Launcher | Verifies deferred companion release does not claim missing Live2D resources; desktop package excludes the deferred companion runtime. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/companion-policy.test.cjs -->
| `desktop/companion-policy.test.cjs` | Desktop Launcher | Verifies companion defaults to enabled with bounded size; legacy click-through is reset once so the companion remains draggable; companion bounds stay inside the selected work area. | none | node:assert/strict; node:test; ./companion-policy.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/component-manager.test.cjs -->
| `desktop/component-manager.test.cjs` | Desktop Launcher | Verifies model downloads classify network, checksum and disk failures; ModelScope embedding mirror excludes unrelated ONNX files; download source selection is explicit for static files and repository providers; CosyVoice model and runtime use the domestic shared-runtime chain; emotion models are absent while the capability is dormant. | none | node:assert/strict; node:crypto; node:fs; node:http; node:os; node:path; node:test; ./component-manager.cjs | filesystem; database/state | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/dashboard-policy.test.cjs -->
| `desktop/dashboard-policy.test.cjs` | Desktop Launcher | Verifies launcher dashboard groups components instead of flattening the homepage; character voices use grouped dropdowns and separate download from activation; voice providers stay switchable after onboarding and the wizard can go back; diagnostics are redacted and exposed through a dedicated IPC contract; Core, web, Launcher and announcements share the release version. | none | node:assert/strict; node:fs; node:path; node:test | filesystem; Electron IPC | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/desktop-architecture.test.cjs -->
| `desktop/desktop-architecture.test.cjs` | Desktop Launcher | Verifies main is an assembly root and each desktop controller has one explicit constructor; IPC channels are unique and preserve the preload contract; service registry and external navigation remain single authorities; local production requires are acyclic and every new controller is called. | none | node:assert/strict; node:fs; node:path; node:test | filesystem; Electron IPC | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/hardware-policy.test.cjs -->
| `desktop/hardware-policy.test.cjs` | Desktop Launcher | Verifies local voice requirements map to the correct service family; hardware policy blocks only the unsupported local service. | none | node:assert/strict; node:test; ./hardware-policy.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/installer-policy.test.cjs -->
| `desktop/installer-policy.test.cjs` | Desktop Launcher | Verifies application upgrades never remove the private environment or user data; all runtime policy modules are included in the packaged application. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/onboarding-policy.test.cjs -->
| `desktop/onboarding-policy.test.cjs` | Desktop Launcher | Verifies text-only onboarding completes without any voice component; remote LLM needs a key while a loopback compatible endpoint does not; voice installation plans keep optional engines separate from base readiness; launcher voice preference follows the product TTS provider; completed base and LLM do not wait for a queued optional voice. | none | node:assert/strict; node:test; ./onboarding-policy.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/process-check.test.cjs -->
| `desktop/process-check.test.cjs` | Desktop Launcher | Verifies process checks do not block the launcher event loop; process checks report a bounded timeout; process check diagnostics retain only the useful tail. | none | node:assert/strict; node:test; ./process-check.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/security-boundaries.test.cjs -->
| `desktop/security-boundaries.test.cjs` | Desktop Launcher | Verifies external navigation rejects dangerous protocols and confirms unknown HTTPS hosts; legacy plaintext API keys migrate into the encrypted secret store. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./external-navigation.cjs; ./secret-store.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/service-policy.test.cjs -->
| `desktop/service-policy.test.cjs` | Desktop Launcher | Verifies core starts before optional local voice services; TTS starts independently of ASR readiness; product entry depends on Core and degrades explicitly to text-only mode; a running core from an older application is stale; crashed services use bounded restart backoff. | none | node:assert/strict; node:fs; node:path; node:test; ./service-policy.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/service-ports.test.cjs -->
| `desktop/service-ports.test.cjs` | Desktop Launcher | Verifies service port overrides are applied to every consumer from one registry. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./service-ports.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/stability-policy.test.cjs -->
| `desktop/stability-policy.test.cjs` | Desktop Launcher | Verifies hardware acceleration is disabled before Electron becomes ready; desktop records and recovers renderer and GPU process failures; chat window has a bounded load timeout and background throttling. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/storage-location.test.cjs -->
| `desktop/storage-location.test.cjs` | Desktop Launcher | Verifies fresh packaged installs keep large data beside the selected install directory; existing LocalAppData payload is preserved and offered a safe migration; an existing packaged deployment wins over stale LocalAppData payload; custom storage location persists outside LocalAppData; storage migration rejects disk roots, nested paths and occupied targets. | none | node:assert/strict; node:fs; node:os; node:path; node:test; ./app-paths.cjs; ./storage-location.cjs | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:frontend/src/App.test.tsx -->
| `frontend/src/App.test.tsx` | Web frontend | Verifies keeps the destiny entry while presenting V2 as the resulting character format; edits only the compact V2 character fields in the library; offers both resume and new conversation actions from the library; requires two clicks before moving a character out of the library; selects the first character when the library data arrives asynchronously. | none | @testing-library/react; @testing-library/user-event; vitest; ./characters/CharacterExperience | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/css-governance.test.ts -->
| `frontend/src/css-governance.test.ts` | Web frontend | Verifies CSS cascade governance; keeps the base stylesheet before the named product override authority; forbids completely identical selector, at-rule context and declaration blocks; allows only reviewed same-selector cascade overrides. | none | node:fs; node:path; postcss; vitest | filesystem | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/SceneExperience.test.tsx -->
| `frontend/src/SceneExperience.test.tsx` | Web frontend | Verifies switches the conversation background directly without starting an activity; uploads a custom background and selects its persistent asset URL; rolls the optimistic scene preview back when binding fails; keeps an uploaded scene retryable and rolls the preview back when binding fails. | none | @testing-library/react; @testing-library/user-event; vitest; ./SceneExperience; ./types | UI/rendering | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/speech.test.ts -->
| `frontend/src/speech.test.ts` | Web frontend | Verifies TTS speech segmentation; removes Chinese and ASCII parenthetical stage directions; discards parenthetical stage directions in voice mode; emits complete sentences for punctuation and ellipsis across chunks; never emits punctuation-only TTS segments and attaches leading ellipsis to prose. | none | vitest; ./speech | none | direct test file | current | Web public; no Core secrets |
<!-- INDEXED:frontend/src/test/setup.ts -->
| `frontend/src/test/setup.ts` | Web frontend | Maintains Verification configuration or execution behavior. | none | @testing-library/react; vitest | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:frontend/vitest.config.ts -->
| `frontend/vitest.config.ts` | Web frontend | Maintains Verification configuration or execution behavior. | none | vitest/config; @vitejs/plugin-react | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:scripts/test-desktop-installer.ps1 -->
| `scripts/test-desktop-installer.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem; process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:tests/test_application_foundation.py -->
| `tests/test_application_foundation.py` | Tests | Verifies test_shared_transaction_rolls_back_every_canonical_store; test_regenerate_withdraws_old_memory_context_and_indexes_atomically; test_alias_identity_resolves_to_the_same_entity_without_duplication; test_profile_schema_migrates_legacy_user_fields_to_compact_v13_document; test_profile_gender_defaults_and_validation_are_explicit. | test_shared_transaction_rolls_back_every_canonical_store; test_regenerate_withdraws_old_memory_context_and_indexes_atomically; test_alias_identity_resolves_to_the_same_entity_without_duplication; test_profile_schema_migrates_legacy_user_fields_to_compact_v13_document; test_profile_gender_defaults_and_validation_are_explicit; test_bm25_plus_and_rrf_keep_independent_rank_evidence; test_openai_usage_extracts_standard_cached_tokens; test_role_audit_writes_one_bounded_continuity_digest_for_next_turn | json; copy; pytest; mindspace_graph.adapters.file_storage; mindspace_graph.adapters.openai_compatible; mindspace_graph.adapters.structured_memory; mindspace_graph.context_ledger; mindspace_graph.entity_registry | filesystem; database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_art_catalog.py -->
| `tests/test_art_catalog.py` | Tests | Verifies test_builtin_art_manifest_v2_and_approved_expansion_are_valid; test_pack_extraction_rolls_back_corrupt_replacement; test_pack_rejects_zip_path_traversal; test_pack_pause_state_preserves_partial_bytes_for_resume. | sha256; test_builtin_art_manifest_v2_and_approved_expansion_are_valid; test_pack_extraction_rolls_back_corrupt_replacement; test_pack_rejects_zip_path_traversal; test_pack_pause_state_preserves_partial_bytes_for_resume | __future__; hashlib; json; zipfile; pathlib; pytest; mindspace_graph.art_catalog | filesystem | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_cancellation.py -->
| `tests/test_cancellation.py` | Tests | Verifies test_interruption_after_model_call_prevents_all_persistence. | CancellingModel; test_interruption_after_model_call_prevents_all_persistence | __future__; pytest; mindspace_graph.adapters.in_memory; mindspace_graph.cancellation; mindspace_graph.graph; mindspace_graph.models; mindspace_graph.ports | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_companion_twenty_round_e2e.py -->
| `tests/test_companion_twenty_round_e2e.py` | Tests | Verifies test_twenty_round_companion_end_to_end. | TwentyRoundCompanionModel; test_twenty_round_companion_end_to_end | __future__; asyncio; json; re; difflib; mindspace_graph.adapters.in_memory; mindspace_graph.models; mindspace_graph.service | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_deploy_installer.py -->
| `tests/test_deploy_installer.py` | Tests | Verifies test_manifest_contains_required_stable_fields_and_matching_hash; test_signature_status_is_detected_from_pe_certificate_table; test_declared_signed_rejects_unsigned_file; test_manifest_rejects_inconsistent_public_identity; test_release_script_never_rewrites_website_html. | SCRIPT; SPEC; write_test_pe; test_manifest_contains_required_stable_fields_and_matching_hash; test_signature_status_is_detected_from_pe_certificate_table; test_declared_signed_rejects_unsigned_file; test_manifest_rejects_inconsistent_public_identity; test_release_script_never_rewrites_website_html | __future__; hashlib; importlib.util; pathlib; pytest | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_emotion_sidechain.py -->
| `tests/test_emotion_sidechain.py` | Tests | Verifies test_emotion_is_forced_off_even_when_legacy_environment_requests_it; test_disabled_emotion_adapter_has_no_work_or_state. | test_emotion_is_forced_off_even_when_legacy_environment_requests_it; test_disabled_emotion_adapter_has_no_work_or_state | __future__; mindspace_graph.emotion_disabled; mindspace_graph.settings | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_file_storage.py -->
| `tests/test_file_storage.py` | Tests | Verifies test_profile_patch_is_atomic_revisioned_and_backed_up; test_regenerate_replaces_round_without_storing_analysis; test_session_projection_names_do_not_collide_and_legacy_files_still_load; test_turn_persists_distinct_user_and_assistant_atomic_times; test_initiative_signal_is_internal_and_excluded_from_recall. | test_profile_patch_is_atomic_revisioned_and_backed_up; test_regenerate_replaces_round_without_storing_analysis; test_session_projection_names_do_not_collide_and_legacy_files_still_load; test_turn_persists_distinct_user_and_assistant_atomic_times; test_initiative_signal_is_internal_and_excluded_from_recall; test_legacy_analysis_is_backed_up_removed_and_filtered; test_delete_assistant_reply_keeps_user_and_creates_pending_event | __future__; json; datetime; mindspace_graph.adapters.file_storage; mindspace_graph.models | filesystem; database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_gpt_sovits_catalog.py -->
| `tests/test_gpt_sovits_catalog.py` | Tests | Verifies test_full_character_catalog_is_version_audited; test_catalog_loader_matches_json_and_keeps_paths_in_model_root; test_catalog_path_guard_rejects_escape. | ROOT; test_full_character_catalog_is_version_audited; test_catalog_loader_matches_json_and_keeps_paths_in_model_root; test_catalog_path_guard_rejects_escape | __future__; json; pathlib; pytest; mindspace_graph.gpt_sovits | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_gpt_sovits_worker.py -->
| `tests/test_gpt_sovits_worker.py` | Tests | Verifies test_v4_voice_prosody_uses_official_punctuation_controls; test_boundary_silence_trim_preserves_cadence_not_long_vocoder_gaps; test_voice_switch_reloads_catalog_for_newly_installed_voice; test_stream_disconnect_is_treated_as_client_cancellation. | ROOT; SPEC; MODULE; test_v4_voice_prosody_uses_official_punctuation_controls; test_boundary_silence_trim_preserves_cadence_not_long_vocoder_gaps; test_voice_switch_reloads_catalog_for_newly_installed_voice; test_stream_disconnect_is_treated_as_client_cancellation | __future__; importlib.util; json; pathlib; numpy | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_graph.py -->
| `tests/test_graph.py` | Tests | Verifies test_short_recall_query_includes_recent_dialogue_anchors; test_specific_long_query_stays_current_only; test_adult_continuity_opens_for_explicit_topic_and_immediate_follow_up; test_happy_path_runs_parallel_retrieval_and_persists_turn; test_output_without_visible_text_fails_once_without_protocol_repair. | invoke; test_short_recall_query_includes_recent_dialogue_anchors; test_specific_long_query_stays_current_only; test_adult_continuity_opens_for_explicit_topic_and_immediate_follow_up; test_happy_path_runs_parallel_retrieval_and_persists_turn; BrokenOnceModel; test_output_without_visible_text_fails_once_without_protocol_repair; PlainTextModel; test_visible_plain_text_uses_deterministic_protocol_fallback_without_second_call; VoiceDirectivePlainTextModel | __future__; json; re; datetime; mindspace_graph.adapters.in_memory; mindspace_graph.graph; mindspace_graph.models; mindspace_graph.nodes | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_maturity_persistence.py -->
| `tests/test_maturity_persistence.py` | Tests | Verifies test_restart_closes_running_run_without_reexecuting_and_keeps_partial; test_run_event_storage_is_idempotent_and_bounded_to_128_events. | test_restart_closes_running_run_without_reexecuting_and_keeps_partial; test_run_event_storage_is_idempotent_and_bounded_to_128_events | __future__; json; mindspace_graph.product_database | database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_openai_compatible.py -->
| `tests/test_openai_compatible.py` | Tests | Verifies test_private_structured_calls_disable_thinking_and_request_json; test_character_structured_generation_records_compatible_usage_kind; test_compaction_disables_thinking_and_requests_json; test_role_audit_uses_structured_compatibility_ladder; test_private_structured_calls_fall_back_for_generic_compatible_servers. | test_private_structured_calls_disable_thinking_and_request_json; test_character_structured_generation_records_compatible_usage_kind; test_compaction_disables_thinking_and_requests_json; test_role_audit_uses_structured_compatibility_ladder; test_private_structured_calls_fall_back_for_generic_compatible_servers; test_stream_retries_connect_handshake_before_first_token; test_visible_stream_disables_thinking_by_default; test_visible_stream_falls_back_when_compatibility_fields_are_rejected; test_native_tool_stream_accumulates_fragmented_call_without_visible_text; test_required_single_native_tool_keeps_first_duplicate_provider_call | json; httpx; mindspace_graph.adapters.in_memory; mindspace_graph.adapters.openai_compatible; mindspace_graph.graph; mindspace_graph.models | network | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_prompt_cache_layout.py -->
| `tests/test_prompt_cache_layout.py` | Tests | Verifies test_next_turn_keeps_confirmed_messages_but_excludes_audit_context; test_compatibility_split_keeps_exactly_the_latest_three_rounds; test_hidden_initiative_trigger_is_not_persisted_as_dialogue_history; test_json_baseline_precedes_history_and_post_history_calibration_is_last; test_native_tool_guidance_is_short_and_old_protocol_is_absent. | profiles; history_through; request; test_next_turn_keeps_confirmed_messages_but_excludes_audit_context; test_compatibility_split_keeps_exactly_the_latest_three_rounds; test_hidden_initiative_trigger_is_not_persisted_as_dialogue_history; test_json_baseline_precedes_history_and_post_history_calibration_is_last; test_native_tool_guidance_is_short_and_old_protocol_is_absent; test_recent_raw_chat_is_not_duplicated_inside_retrieval_context; test_old_raw_chat_can_return_through_rag_outside_direct_history_window | __future__; copy; mindspace_graph.adapters.file_storage; mindspace_graph.context_ledger; mindspace_graph.models; mindspace_graph.native_tools; mindspace_graph.prompting | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_r18_private_library.py -->
| `tests/test_r18_private_library.py` | Tests | Verifies test_private_library_reads_a_sealed_docx_from_memory_only; test_node_packager_and_core_unsealer_share_one_envelope_format; test_tampered_private_library_is_rejected. | test_private_library_reads_a_sealed_docx_from_memory_only; test_node_packager_and_core_unsealer_share_one_envelope_format; test_tampered_private_library_is_rejected | __future__; io; subprocess; zipfile; pathlib; mindspace_graph.r18_private_library | process execution | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_roleplay.py -->
| `tests/test_roleplay.py` | Tests | Verifies test_profile_v13_initializes_roleplay_sections_without_overwriting; test_legacy_profile_migration_preserves_existing_character_content; test_raw_assistant_prose_is_never_returned_by_chat_retrieval; test_acknowledgement_and_bare_scene_transition_skip_raw_chat_retrieval; test_presentation_auto_defaults_to_dialogue_and_routes_explicit_action_to_scene. | request; profiles; test_profile_v13_initializes_roleplay_sections_without_overwriting; test_legacy_profile_migration_preserves_existing_character_content; test_raw_assistant_prose_is_never_returned_by_chat_retrieval; test_acknowledgement_and_bare_scene_transition_skip_raw_chat_retrieval; test_presentation_auto_defaults_to_dialogue_and_routes_explicit_action_to_scene; test_roleplay_temperature_is_dynamic_and_never_raises_user_setting; test_presentation_override_and_scene_continuation_are_observable; test_dialogue_projection_drops_only_legacy_stage_openers_and_preserves_normal_parentheses | __future__; json; mindspace_graph.adapters.file_storage; mindspace_graph.adapters.local_retriever; mindspace_graph.models; mindspace_graph.prompting; mindspace_graph.r18_director; mindspace_graph.roleplay | filesystem; database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_shared_chapters.py -->
| `tests/test_shared_chapters.py` | Tests | Verifies test_journals_and_moments_are_character_isolated_and_narrative_only; test_journal_uses_character_first_person_and_complete_user_anchored_rounds; test_journal_invalid_perspective_falls_back_but_counts_attempt; test_activity_actions_are_revisioned_idempotent_and_character_bound; test_conversation_scene_is_session_scoped_and_inherits_character_default. | make_app; test_journals_and_moments_are_character_isolated_and_narrative_only; test_journal_uses_character_first_person_and_complete_user_anchored_rounds; test_journal_invalid_perspective_falls_back_but_counts_attempt; test_activity_actions_are_revisioned_idempotent_and_character_bound; test_conversation_scene_is_session_scoped_and_inherits_character_default; test_archived_character_session_scene_returns_controlled_gone_response; test_activity_context_is_resolved_server_side_for_chat; test_prompt_inspector_marks_scene_as_ephemeral_system_layer; test_confirmed_continuity_migration_is_idempotent | __future__; copy; pytest; fastapi.testclient; mindspace_graph.adapters.in_memory; mindspace_graph.api; mindspace_graph.models; mindspace_graph.settings | database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_stage1_hardening.py -->
| `tests/test_stage1_hardening.py` | Tests | Verifies test_settings_refresh_failure_restores_config_profile_and_clears_journal; test_startup_recovers_legacy_settings_journal_without_transaction_id; test_settings_rollback_preserves_a_concurrent_profile_edit; test_request_digest_rejects_changed_payload_after_restart. | test_settings_refresh_failure_restores_config_profile_and_clears_journal; test_startup_recovers_legacy_settings_journal_without_transaction_id; test_settings_rollback_preserves_a_concurrent_profile_edit; test_request_digest_rejects_changed_payload_after_restart | __future__; copy; fastapi.testclient; mindspace_graph.api; mindspace_graph.models; mindspace_graph.settings | database/state | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_streaming_protocol.py -->
| `tests/test_streaming_protocol.py` | Tests | Verifies test_incremental_response_parser_handles_every_chunk_boundary; test_incremental_response_parser_never_leaks_json_update; test_incremental_response_parser_accepts_plain_leading_reply_without_leaking_json; test_protocol_parser_recovers_plain_leading_response; test_protocol_parser_recovers_plain_response_that_starts_with_voice_directive. | test_incremental_response_parser_handles_every_chunk_boundary; test_incremental_response_parser_never_leaks_json_update; test_incremental_response_parser_accepts_plain_leading_reply_without_leaking_json; test_protocol_parser_recovers_plain_leading_response; test_protocol_parser_recovers_plain_response_that_starts_with_voice_directive; test_protocol_parser_accepts_fenced_json_and_dangling_response_close; test_model_terminator_is_removed_from_stream_and_final_response | __future__; mindspace_graph.protocol | none | direct test file | current | Development-only; not runtime payload by default |

## Version and release (60)

| Path | Layer | Precise responsibility | Public entry / exports | Dependencies / direction | Data / side effects | Tests / gates | Status | Encryption / release boundary |
|---|---|---|---|---|---|---|---|---|
<!-- INDEXED:.github/release-notes/v0.5.7.md -->
| `.github/release-notes/v0.5.7.md` | Governance/config | Documents “Mindspace 0.5.7” with current authority. | Mindspace 0.5.7 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:config/core-release-allowlist.json -->
| `config/core-release-allowlist.json` | Governance/config | Positive allowlist for Core update source trees and explicit runtime files. | schema_version; targets; source_trees; runtime_files | none | none | desktop/release-targets.test.cjs; scripts/verify-repository-policy.mjs | current | Release/config contract; never contains secrets |
<!-- INDEXED:config/version.json -->
| `config/version.json` | Governance/config | Canonical product and signed-runtime version contract consumed by synchronization and verification tools. | schema_version; product_version; release_date; release_title; release_status; release_summary; runtime_bundle | none | none | node scripts/verify-version-consistency.mjs | current | Release/config contract; never contains secrets |
<!-- INDEXED:deploy/nginx/mindspace-runtime-redirects.conf -->
| `deploy/nginx/mindspace-runtime-redirects.conf` | Packaging adapter | Mindspace low-bandwidth runtime map. | none | none | none | repository policy / full suite | current | Repository governance; inspect allowlist before release |
<!-- INDEXED:desktop/assets/runtime-manifest.json -->
| `desktop/assets/runtime-manifest.json` | Desktop Launcher | Defines governed Version and release data with top-level keys: schema_version; runtime_version; platform; arch; components; signature. | schema_version; runtime_version; platform; arch; components; signature | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/assets/update-public-key.pem -->
| `desktop/assets/update-public-key.pem` | Desktop Launcher | BEGIN PUBLIC KEY----- | none | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/launcher-updater.cjs -->
| `desktop/launcher-updater.cjs` | Desktop Launcher | const { EventEmitter } = require("node:events"); | module.exports | node:events; electron-updater | network | desktop/launcher-updater.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/launcher-updater.test.cjs -->
| `desktop/launcher-updater.test.cjs` | Desktop Launcher | Verifies update-available; download-progress; update-downloaded; launcher updater exposes check, progress and install state; launcher updater refuses insecure public feeds. | none | node:assert/strict; node:test; node:events; ./launcher-updater.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/package-lock.json -->
| `desktop/package-lock.json` | Desktop Launcher | Defines governed Version and release data with top-level keys: name; version; lockfileVersion; requires; packages. | name; version; lockfileVersion; requires; packages | none | filesystem; process execution | repository policy / full suite | generated | Launcher public; no protected Core source |
<!-- INDEXED:desktop/package.json -->
| `desktop/package.json` | Desktop Launcher | Defines governed Version and release data with top-level keys: name; version; description; author; private; main; scripts; dependencies. | name; version; description; author; private; main; scripts; dependencies; devDependencies; build | none | none | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/qwen-runtime-policy.cjs -->
| `desktop/qwen-runtime-policy.cjs` | Desktop Launcher | const MINIMUM_QWEN_VRAM_MIB = 15_500; | module.exports | none | none | desktop/qwen-runtime-policy.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/qwen-runtime-policy.test.cjs -->
| `desktop/qwen-runtime-policy.test.cjs` | Desktop Launcher | Verifies Qwen preflight refuses unsupported machines before any install; Qwen preflight accepts only a clear managed runtime path. | none | node:test; node:assert/strict; ./qwen-runtime-policy.cjs | none | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/release-policy.test.cjs -->
| `desktop/release-policy.test.cjs` | Desktop Launcher | Verifies production web and Core release policy reject source maps and internal scripts. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/release-targets.test.cjs -->
| `desktop/release-targets.test.cjs` | Desktop Launcher | Verifies Core builder and updater consume one positive target allowlist. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/runtime-assets.test.cjs -->
| `desktop/runtime-assets.test.cjs` | Desktop Launcher | Verifies packaged GPT-SoVITS catalog is generated from the Core authority. | none | node:assert/strict; node:fs; node:path; node:test | filesystem | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/runtime-controller.cjs -->
| `desktop/runtime-controller.cjs` | Desktop Launcher | const { spawn, spawnSync } = require("node:child_process"); | module.exports; IPC launcher:component; IPC runtime:action; IPC runtime:snapshot; IPC runtime:install; IPC runtime:cancel; IPC runtime:retry; IPC runtime:repair; IPC runtime:source; IPC runtime:proxy | node:child_process; node:fs; node:path | filesystem; Electron IPC; process execution; environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/runtime-manager.cjs -->
| `desktop/runtime-manager.cjs` | Desktop Launcher | const crypto = require("node:crypto"); | module.exports | node:crypto; node:fs; node:os; node:path; node:child_process; ./update-manager.cjs | filesystem; network; process execution; environment | desktop/runtime-manager.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/runtime-manager.test.cjs -->
| `desktop/runtime-manager.test.cjs` | Desktop Launcher | Verifies runtime promotion retries transient Windows file locks; runtime failures expose stable diagnostic codes; private Python processes do not inherit host interpreter state; Python completeness requires the standard-library encoding landmarks; production runtime manifest uses the live control endpoint and remains signed. | none | node:assert/strict; node:crypto; node:events; node:fs; node:http; node:os; node:path; node:test | filesystem; process execution | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/runtime-smoke.cjs -->
| `desktop/runtime-smoke.cjs` | Desktop Launcher | const { app, net } = require("electron"); | none | electron; node:fs; node:path; extract-zip; ./runtime-manager.cjs; ./component-manager.cjs | filesystem; network | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/sync-runtime-assets.cjs -->
| `desktop/sync-runtime-assets.cjs` | Desktop Launcher | const fs = require("node:fs"); | none | node:fs; node:path | filesystem | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/update-e2e.cjs -->
| `desktop/update-e2e.cjs` | Desktop Launcher | const fs = require("node:fs"); | none | node:fs; node:path; ./update-manager.cjs | environment | repository policy / full suite | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/update-manager.cjs -->
| `desktop/update-manager.cjs` | Desktop Launcher | const crypto = require("node:crypto"); | module.exports | node:crypto; node:fs; node:path; node:child_process | filesystem; process execution; environment | desktop/update-manager.test.cjs | current | Launcher public; no protected Core source |
<!-- INDEXED:desktop/update-manager.test.cjs -->
| `desktop/update-manager.test.cjs` | Desktop Launcher | Verifies semantic versions are ordered for update decisions; signed manifests verify and tampering is rejected; production update feeds require HTTPS; signed v2 catalog verifies and prevents tampering; rollout bucket is deterministic and mandatory boundaries are explicit. | none | node:assert/strict; node:crypto; node:fs; node:os; node:path; node:test; ./update-manager.cjs | filesystem; network | direct test file | current | Launcher public; no protected Core source |
<!-- INDEXED:docs/ONLINE_UPDATE_RELEASE.md -->
| `docs/ONLINE_UPDATE_RELEASE.md` | Documentation | Documents “Mindspace 在线更新发布” with current authority. | Mindspace 在线更新发布 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/RELEASE_VERIFICATION_0.6.0.md -->
| `docs/RELEASE_VERIFICATION_0.6.0.md` | Documentation | Documents “Mindspace 0.6.0 发布验证记录” with historical authority. | Mindspace 0.6.0 发布验证记录 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/RELEASE_VERIFICATION_0.7.0.md -->
| `docs/RELEASE_VERIFICATION_0.7.0.md` | Documentation | Documents “Mindspace 0.7.0 本地正式发布验证” with historical authority. | Mindspace 0.7.0 本地正式发布验证 | linked current/historical documentation | none | repository policy / full suite | historical | Development-only; not runtime payload by default |
<!-- INDEXED:docs/release-history.json -->
| `docs/release-history.json` | Documentation | Defines governed Version and release data with top-level keys: 0; 1; 2; 3; 4; 5; 6; 7. | 0; 1; 2; 3; 4; 5; 6; 7; 8; 9 | none | none | repository policy / full suite | generated | Development-only; not runtime payload by default |
<!-- INDEXED:docs/RUNTIME_RUNBOOK.md -->
| `docs/RUNTIME_RUNBOOK.md` | Documentation | Documents “0.9.0 Runtime Runbook” with current authority. | 0.9.0 Runtime Runbook | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/UPDATE_AND_CAPACITY.md -->
| `docs/UPDATE_AND_CAPACITY.md` | Documentation | Documents “更新发布与容量基线” with prototype authority. | 更新发布与容量基线 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:docs/VERSIONING_AND_GENERATED_ASSETS.md -->
| `docs/VERSIONING_AND_GENERATED_ASSETS.md` | Documentation | Documents “版本与生成资产责任” with current authority. | 版本与生成资产责任 | linked current/historical documentation | none | repository policy / full suite | current | Development-only; not runtime payload by default |
<!-- INDEXED:docs/ZERO_ENVIRONMENT_RUNTIME.md -->
| `docs/ZERO_ENVIRONMENT_RUNTIME.md` | Documentation | Documents “零环境运行时” with prototype authority. | 零环境运行时 | linked current/historical documentation | none | repository policy / full suite | prototype | Development-only; not runtime payload by default |
<!-- INDEXED:frontend/package-lock.json -->
| `frontend/package-lock.json` | Web frontend | Defines governed Version and release data with top-level keys: name; version; lockfileVersion; requires; packages. | name; version; lockfileVersion; requires; packages | none | none | repository policy / full suite | generated | Web public; no Core secrets |
<!-- INDEXED:frontend/package.json -->
| `frontend/package.json` | Web frontend | Defines governed Version and release data with top-level keys: name; private; version; type; scripts; dependencies; devDependencies. | name; private; version; type; scripts; dependencies; devDependencies | none | none | repository policy / full suite | current | Web public; no Core secrets |
<!-- INDEXED:scripts/apply-update.ps1 -->
| `scripts/apply-update.ps1` | Developer tooling | [CmdletBinding(DefaultParameterSetName = 'Apply')] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/build-update.ps1 -->
| `scripts/build-update.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/excludes-gpt-sovits-runtime.txt -->
| `scripts/excludes-gpt-sovits-runtime.txt` | Developer tooling | CUDA Torch is supplied by the verified ASR environment through a read-only | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/generate-update-key.mjs -->
| `scripts/generate-update-key.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:crypto; node:fs; node:path; node:process | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/package.ps1 -->
| `scripts/package.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/prepare-online-release.ps1 -->
| `scripts/prepare-online-release.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/publish-online-release-interactive.ps1 -->
| `scripts/publish-online-release-interactive.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/publish-online-release.ps1 -->
| `scripts/publish-online-release.ps1` | Developer tooling | [CmdletBinding(DefaultParameterSetName = 'Local')] | none | none | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/release-catalog.mjs -->
| `scripts/release-catalog.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:crypto; node:fs; node:path | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/release-manifest.mjs -->
| `scripts/release-manifest.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:crypto; node:fs; node:path; node:process | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/requirements-gpt-sovits-runtime.txt -->
| `scripts/requirements-gpt-sovits-runtime.txt` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/runtime-hardening.test.ps1 -->
| `scripts/runtime-hardening.test.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem | scripts/runtime-hardening.test.ps1 | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/runtime-verify.ps1 -->
| `scripts/runtime-verify.ps1` | Developer tooling | [CmdletBinding()] | none | none | environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/sign-runtime-manifest.mjs -->
| `scripts/sign-runtime-manifest.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:crypto; node:fs; node:path | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/smoke_core_package.py -->
| `scripts/smoke_core_package.py` | Developer tooling | Load an extracted Core package in an isolated runtime and probe its HTTP app. | main | __future__; json; os; sys; pathlib | environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/smoke-core-package.ps1 -->
| `scripts/smoke-core-package.ps1` | Developer tooling | [CmdletBinding()] | none | none | none | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/sync-version.mjs -->
| `scripts/sync-version.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:fs; node:path; node:url | filesystem; database/state; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/test-update-e2e.ps1 -->
| `scripts/test-update-e2e.ps1` | Developer tooling | [CmdletBinding()] | none | none | filesystem; process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/test-zero-env-runtime.cjs -->
| `scripts/test-zero-env-runtime.cjs` | Developer tooling | const fs = require("node:fs"); | none | node:fs; node:path; node:child_process; ../desktop/node_modules/extract-zip; ../desktop/runtime-manager.cjs; ../desktop/component-manager.cjs | filesystem; network; process execution | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/update-readme-history.mjs -->
| `scripts/update-readme-history.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:fs; node:path | filesystem | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-online-release.mjs -->
| `scripts/verify-online-release.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:crypto; node:fs; node:path | filesystem; network | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:scripts/verify-version-consistency.mjs -->
| `scripts/verify-version-consistency.mjs` | Developer tooling | Maintains Version and release configuration or execution behavior. | none | node:fs; node:path; node:url | filesystem; database/state; environment | repository policy / full suite | current | Developer tool; release only when allowlisted |
<!-- INDEXED:src/mindspace_graph/role_runtime.py -->
| `src/mindspace_graph/role_runtime.py` | Core backend | Compact, confirmed role state shared by every chat provider. | DEFAULT_USER_NAME; build_runtime_role_state; compact_system_prompt; compact_turn_directive | __future__; typing | none | tests/test_role_runtime.py | current | Core protected release surface |
<!-- INDEXED:src/mindspace_graph/version.py -->
| `src/mindspace_graph/version.py` | Core backend | Build version synchronized from the project release source. | APP_VERSION | none | none | tests/test_version_release_governance.py | current | Core protected release surface |
<!-- INDEXED:tests/test_json_update_policy.py -->
| `tests/test_json_update_policy.py` | Tests | Verifies test_server_fields_and_parent_objects_are_never_writable; test_history_and_retrieval_cannot_be_write_evidence; test_deletion_reconciliation_requires_a_current_pending_event; test_stale_revision_blocks_the_whole_update; test_registry_normalizes_model_friendly_scalar_and_list_operations. | bundle; patch; plan; test_server_fields_and_parent_objects_are_never_writable; test_history_and_retrieval_cannot_be_write_evidence; test_deletion_reconciliation_requires_a_current_pending_event; test_stale_revision_blocks_the_whole_update; test_registry_normalizes_model_friendly_scalar_and_list_operations; test_registry_normalizes_single_value_written_to_a_list_base_path; test_current_agent_can_write_only_a_directly_spoken_agent_value | __future__; mindspace_graph.adapters.in_memory; mindspace_graph.models; mindspace_graph.policies | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_role_runtime.py -->
| `tests/test_role_runtime.py` | Tests | Verifies test_v2_runtime_state_prefers_saved_name_alias_and_memory; test_compact_prompt_excludes_v2_examples; test_v2_card_keeps_per_character_user_name. | test_v2_runtime_state_prefers_saved_name_alias_and_memory; test_compact_prompt_excludes_v2_examples; test_v2_card_keeps_per_character_user_name | mindspace_graph.character_card; mindspace_graph.role_runtime | none | direct test file | current | Development-only; not runtime payload by default |
<!-- INDEXED:tests/test_version_release_governance.py -->
| `tests/test_version_release_governance.py` | Tests | Verifies test_sync_generates_source_tree_paths_and_independent_verifier_accepts_them; test_sync_fails_closed_for_invalid_allowlist_schema_or_paths; test_independent_verifier_rejects_numeric_duplicate_and_mismatched_payload_targets; test_independent_verifier_does_not_delegate_to_sync_script. | ROOT; SYNC_SCRIPT; VERIFY_SCRIPT; FIXTURE_FILES; sandbox; run_node; read_json; write_json; test_sync_generates_source_tree_paths_and_independent_verifier_accepts_them; test_sync_fails_closed_for_invalid_allowlist_schema_or_paths | __future__; json; os; shutil; subprocess; pathlib; pytest | filesystem; process execution; environment | direct test file | current | Development-only; not runtime payload by default |
