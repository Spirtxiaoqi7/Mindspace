"""Low-latency PCM streaming ASR with FunASR and deterministic fallbacks."""

from __future__ import annotations

import math
import re
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from threading import Condition, RLock
from time import monotonic, perf_counter
from typing import Any


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return str(result.get("text") or result.get("value") or "").strip()
    if isinstance(result, list):
        return "".join(_result_text(item) for item in result).strip()
    return ""


def _vad_has_speech(result: Any) -> bool:
    if isinstance(result, dict):
        return _vad_has_speech(result.get("value") or result.get("text") or [])
    if not isinstance(result, list):
        return False
    if len(result) >= 2 and all(isinstance(item, (int, float)) for item in result[:2]):
        return float(result[0]) >= 0
    return any(_vad_has_speech(item) for item in result)


def _runtime_slot(runtime: Any, priority: str = "stream"):
    scheduler = getattr(runtime, "_scheduler", None)
    if scheduler is not None:
        return scheduler.slot(priority)
    lock = getattr(runtime, "_inference_lock", None)
    return lock if lock is not None else nullcontext(True)


STOP_COMMANDS = {"停", "暂停", "等等", "等一下", "打住", "别说了", "先别说"}
FILLER_ONLY = {"", "嗯", "啊", "呃", "额", "哦", "诶", "唉", "哎"}


def _compact_speech_text(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text).lower()


def _text_similarity(left: str, right: str) -> float:
    left, right = _compact_speech_text(left), _compact_speech_text(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _looks_like_echo(text: str, playback_text: str) -> bool:
    normalized = _compact_speech_text(text)
    playback = _compact_speech_text(playback_text)
    if len(normalized) < 2 or len(playback) < 4:
        return False
    return normalized in playback or _text_similarity(normalized, playback) >= 0.82


def _split_refinement_uncertainty(
    stream_text: str, final_text: str
) -> tuple[str, list[dict[str, str]]]:
    """Keep agreeing characters as the backbone and mark changed spans as alternatives."""

    stream = _compact_speech_text(stream_text)
    final = _compact_speech_text(final_text)
    if not stream or not final:
        return "", []
    matcher = SequenceMatcher(None, stream, final)
    confirmed: list[str] = []
    uncertain: list[dict[str, str]] = []
    for tag, _left_start, _left_end, right_start, right_end in matcher.get_opcodes():
        fragment = final[right_start:right_end]
        if tag == "equal":
            confirmed.append(fragment)
        elif fragment:
            uncertain.append({"text": fragment, "reason": "stream_final_disagreement"})
    return "".join(confirmed), uncertain


def apply_asr_decision(event: dict[str, Any]) -> dict[str, Any]:
    """Classify a final transcript without another model call or persistent side effect."""

    data = event.setdefault("data", {})
    text = str(data.get("text") or "").strip()
    compact = _compact_speech_text(text)
    playback_text = str(data.get("playback_text") or "")
    playback_active = bool(data.get("playback_active"))
    vad_confirmed = bool(data.get("vad_confirmed"))
    stable_partial = bool(data.get("stable_partial"))
    explicit_stop = compact in STOP_COMMANDS
    reasons: list[str] = []
    if vad_confirmed:
        reasons.append("vad_confirmed")
    if stable_partial:
        reasons.append("stable_partial")
    if explicit_stop:
        reasons.append("explicit_stop")
    if data.get("correction_matches"):
        reasons.append("vocabulary_match")

    quality = "accepted"
    confirmed_text = text
    uncertain_segments: list[dict[str, str]] = []
    if compact in FILLER_ONLY:
        quality, confirmed_text = "rejected", ""
        reasons.append("filler_only")
    elif not compact:
        quality, confirmed_text = "rejected", ""
        reasons.append("no_meaningful_text")
    elif playback_active and not vad_confirmed:
        quality, confirmed_text = "rejected", ""
        reasons.append("playback_without_vad")
    elif playback_active and _looks_like_echo(text, playback_text):
        quality, confirmed_text = "rejected", ""
        reasons.append("playback_echo")
    elif len(set(compact)) <= 2 and len(compact) >= 6:
        quality, confirmed_text = "rejected", ""
        reasons.append("abnormal_repetition")
    else:
        stream_text = str(data.get("stream_text") or data.get("raw_text") or "")
        refinement_applied = bool((data.get("refinement") or {}).get("applied"))
        agreement = _text_similarity(stream_text, text) if stream_text and text else 1.0
        data["stream_final_agreement"] = round(agreement, 4)
        if refinement_applied and agreement < 0.72:
            confirmed_text, uncertain_segments = _split_refinement_uncertainty(
                stream_text, text
            )
            if len(_compact_speech_text(confirmed_text)) < 2:
                confirmed_text = ""
            quality = "uncertain"
            reasons.append("stream_final_disagreement")
        elif playback_active and not (explicit_stop or stable_partial):
            # A plausible final remains draft-only when playback prevented a
            # stable partial and the final pass was skipped for echo safety.
            quality, confirmed_text = "uncertain", ""
            uncertain_segments = [{"text": text, "reason": "playback_unstable_text"}]
            reasons.append("playback_unstable_text")

    data.update(
        {
            "quality": quality,
            "confirmed_text": confirmed_text,
            "uncertain_segments": uncertain_segments,
            "barge_in_eligible": bool(
                playback_active and confirmed_text and quality in {"accepted", "uncertain"}
            ),
            "explicit_stop": explicit_stop,
            "decision_reasons": reasons,
        }
    )
    return event


@dataclass(slots=True)
class ASRSessionOptions:
    sample_rate: int = 16000
    silence_ms: int = 650
    energy_threshold: float = 10 ** (-35 / 20)
    min_speech_ms: int = 120
    candidate_release_ms: int = 240
    playback_active: bool = False
    playback_text: str = ""
    auto_send: bool = True
    vocabulary_revision: str = ""
    decoder_hotwords: tuple[str, ...] = ()
    explicit_corrections: dict[str, str] | None = None
    fuzzy_targets: tuple[dict[str, Any], ...] = ()
    deferred_during_playback: bool = True
    input_locked: bool = False
    tail_resume_min_ms: int = 100
    tail_resume_energy_ratio: float = 2.4
    dynamic_endpointing: bool = True
    final_refinement_enabled: bool = True
    final_refinement_timeout_ms: int = 1400
    final_refinement_min_audio_ms: int = 320
    final_refinement_max_audio_ms: int = 15000


class GPUInferenceScheduler:
    """Serialize one GPU while allowing streaming ASR to jump ahead of final passes."""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._active = ""
        self._waiting_streams = 0
        self._waiting_finals = 0
        self._last_stream_finished = 0.0

    @contextmanager
    def slot(
        self,
        priority: str,
        *,
        timeout: float | None = None,
        idle_grace: float = 0.0,
    ):
        stream = priority == "stream"
        deadline = None if timeout is None else monotonic() + max(0.0, timeout)
        acquired = False
        with self._condition:
            if stream:
                self._waiting_streams += 1
            else:
                self._waiting_finals += 1
            try:
                while True:
                    now = monotonic()
                    stream_has_priority = not stream and self._waiting_streams > 0
                    grace_pending = (
                        not stream
                        and idle_grace > 0
                        and self._last_stream_finished > 0
                        and now - self._last_stream_finished < idle_grace
                    )
                    if not self._active and not stream_has_priority and not grace_pending:
                        self._active = priority
                        acquired = True
                        break
                    if deadline is not None:
                        remaining = deadline - now
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                    else:
                        self._condition.wait()
            finally:
                if stream:
                    self._waiting_streams -= 1
                else:
                    self._waiting_finals -= 1
        try:
            yield acquired
        finally:
            if acquired:
                with self._condition:
                    if stream:
                        self._last_stream_finished = monotonic()
                    self._active = ""
                    self._condition.notify_all()

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "active": self._active,
                "waiting_streams": self._waiting_streams,
                "waiting_finals": self._waiting_finals,
            }


class ASRTextCorrector:
    """Small deterministic corrector; no chat-model call and no prompt mutation."""

    def __init__(self, options: ASRSessionOptions) -> None:
        self.explicit = dict(options.explicit_corrections or {})
        self.targets = tuple(options.fuzzy_targets or ())
        self._pinyin: Any | None = None
        self._fuzz: Any | None = None
        self._target_keys: list[tuple[dict[str, Any], str]] = []
        if self.targets:
            try:
                from pypinyin import lazy_pinyin
                from rapidfuzz import fuzz

                self._pinyin = lazy_pinyin
                self._fuzz = fuzz
                self._target_keys = [
                    (item, "".join(lazy_pinyin(str(item.get("term") or ""))).lower())
                    for item in self.targets
                    if str(item.get("term") or "")
                ]
            except ImportError:
                self._pinyin = None
                self._fuzz = None

    def apply(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        updated = text
        matches: list[dict[str, Any]] = []
        for wrong in sorted(self.explicit, key=len, reverse=True):
            right = self.explicit[wrong]
            if wrong not in updated:
                continue
            matches.append(
                {
                    "from": wrong,
                    "to": right,
                    "score": 1.0,
                    "source": "explicit",
                }
            )
            updated = updated.replace(wrong, right)
        if self._pinyin is None or self._fuzz is None or not self._target_keys:
            return updated, matches

        # Longest and highest-confidence non-overlapping candidates win.  Single
        # Chinese characters are never fuzzy-replaced.
        candidates: list[dict[str, Any]] = []
        for item, target_key in self._target_keys:
            term = str(item.get("term") or "")
            if len(term) < 2 or term in updated:
                continue
            threshold = float(item.get("threshold") or 0.96)
            for length in range(max(2, len(term) - 1), len(term) + 2):
                for start in range(0, max(0, len(updated) - length + 1)):
                    segment = updated[start : start + length]
                    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", segment):
                        continue
                    key = "".join(self._pinyin(segment)).lower()
                    score = self._fuzz.ratio(key, target_key) / 100.0
                    if score >= threshold:
                        candidates.append(
                            {
                                "start": start,
                                "end": start + length,
                                "from": segment,
                                "to": term,
                                "score": round(score, 4),
                                "source": item.get("source") or "vocabulary",
                                "source_field": item.get("source_field") or "",
                            }
                        )
        occupied: list[tuple[int, int]] = []
        selected: list[dict[str, Any]] = []
        for item in sorted(
            candidates,
            key=lambda value: (float(value["score"]), int(value["end"]) - int(value["start"])),
            reverse=True,
        ):
            span = (int(item["start"]), int(item["end"]))
            if any(not (span[1] <= left or span[0] >= right) for left, right in occupied):
                continue
            occupied.append(span)
            selected.append(item)
        for item in sorted(selected, key=lambda value: int(value["start"]), reverse=True):
            updated = updated[: int(item["start"])] + str(item["to"]) + updated[int(item["end"]) :]
        matches.extend(sorted(selected, key=lambda value: int(value["start"])))
        return updated, matches


def apply_final_refinement(
    event: dict[str, Any],
    result: dict[str, Any],
    corrector: ASRTextCorrector,
) -> dict[str, Any]:
    """Replace a final transcript only after a successful bounded second pass."""

    data = event.setdefault("data", {})
    refined_text = str(result.get("text") or "").strip()
    metadata = {key: value for key, value in result.items() if key != "text"}
    data["refinement"] = metadata
    if not result.get("applied") or not refined_text:
        return apply_asr_decision(event)
    stream_text = str(data.get("raw_text") or data.get("text") or "").strip()
    corrected_text, matches = corrector.apply(refined_text)
    data["stream_text"] = stream_text
    data["raw_text"] = refined_text
    data["text"] = corrected_text
    data["correction_matches"] = matches
    return apply_asr_decision(event)


class FunASRRuntime:
    """Load large models once, while keeping per-connection inference caches isolated."""

    def __init__(self, model_root: Path, device: str = "cuda:0") -> None:
        self.model_root = model_root
        self.device = device
        self.asr: Any | None = None
        self.vad: Any | None = None
        self.punc: Any | None = None
        self.final_asr: Any | None = None
        self.error = ""
        self.final_error = ""
        self._attempted = False
        self._final_attempted = False
        self._lock = RLock()
        self._final_lock = RLock()
        self._scheduler = GPUInferenceScheduler()

    def load(self) -> bool:
        if self.asr is not None:
            return True
        with self._lock:
            if self._attempted:
                return self.asr is not None
            self._attempted = True
            try:
                from funasr import AutoModel

                asr_path = self.model_root / "paraformer-zh-streaming"
                vad_path = self.model_root / "fsmn-vad"
                punc_path = self.model_root / "ct-punc"
                self.asr = AutoModel(
                    model=str(asr_path if asr_path.exists() else "paraformer-zh-streaming"),
                    device=self.device,
                    disable_update=True,
                    disable_pbar=True,
                )
                self.vad = AutoModel(
                    model=str(vad_path if vad_path.exists() else "fsmn-vad"),
                    device=self.device,
                    disable_update=True,
                    disable_pbar=True,
                )
                self.punc = AutoModel(
                    model=str(punc_path if punc_path.exists() else "ct-punc"),
                    device=self.device,
                    disable_update=True,
                    disable_pbar=True,
                )
            except Exception as exc:  # noqa: BLE001 - reported through health and WS
                self.error = str(exc)
                self.asr = None
                self.vad = None
                self.punc = None
            return self.asr is not None

    @property
    def final_model_path(self) -> Path:
        return self.model_root / "Fun-ASR-Nano-2512"

    def load_refiner(self) -> bool:
        """Load the optional final-pass model once without changing streaming readiness."""

        if self.final_asr is not None:
            return True
        if not self.final_model_path.exists():
            self.final_error = "Fun-ASR-Nano-2512 is not installed"
            return False
        with self._final_lock:
            if self._final_attempted:
                return self.final_asr is not None
            self._final_attempted = True
            try:
                from funasr import AutoModel

                # Model loading is also serialized with inference so Launcher startup
                # cannot overlap a large final model transfer with streaming CUDA work.
                with self._scheduler.slot("final_load") as acquired:
                    if not acquired:
                        self._final_attempted = False
                        return False
                    self.final_asr = AutoModel(
                        model=str(self.final_model_path),
                        device=self.device,
                        disable_update=True,
                        disable_pbar=True,
                    )
                    warmup = self.final_model_path / "example" / "zh.mp3"
                    if warmup.exists():
                        warmup_result = self.final_asr.generate(
                            input=[str(warmup)],
                            batch_size=1,
                            language="中文",
                            itn=True,
                            max_length=64,
                        )
                        if not _result_text(warmup_result):
                            raise RuntimeError("Fun-ASR Nano warmup returned no text")
            except Exception as exc:  # noqa: BLE001 - exposed through worker health
                self.final_error = str(exc)
                self.final_asr = None
            return self.final_asr is not None

    def status(self) -> dict[str, Any]:
        installed = find_spec("funasr") is not None
        model_files = {
            name: (self.model_root / name).exists()
            for name in ("paraformer-zh-streaming", "fsmn-vad", "ct-punc")
        }
        final_installed = self.final_model_path.exists()
        ready = self.asr is not None or (installed and all(model_files.values()))
        return {
            "ready": ready,
            "installed": installed,
            "provider": "funasr",
            "device": self.device,
            "model_root": str(self.model_root),
            "models": model_files,
            "error": self.error,
            "final_model": {
                "name": "Fun-ASR-Nano-2512",
                "installed": final_installed,
                "loaded": self.final_asr is not None,
                "ready": self.final_asr is not None,
                "error": self.final_error,
            },
            "scheduler": self._scheduler.status(),
        }

    def refine_final_pcm(
        self,
        pcm: bytes,
        options: ASRSessionOptions,
        *,
        playback_active: bool,
    ) -> dict[str, Any]:
        """Run one bounded, low-priority final pass or return an explicit fallback reason."""

        duration_ms = int(len(pcm) / (options.sample_rate * 2) * 1000)
        fallback: dict[str, Any] = {
            "applied": False,
            "model": "Fun-ASR-Nano-2512",
            "duration_ms": duration_ms,
        }
        if not options.final_refinement_enabled:
            return {**fallback, "reason": "disabled"}
        if playback_active:
            return {**fallback, "reason": "playback_echo_risk"}
        if duration_ms < options.final_refinement_min_audio_ms:
            return {**fallback, "reason": "audio_too_short"}
        if duration_ms > options.final_refinement_max_audio_ms:
            return {**fallback, "reason": "audio_too_long"}
        if self.final_asr is None:
            return {
                **fallback,
                "reason": "model_not_ready",
                "error": self.final_error,
            }

        import numpy as np
        import torch

        samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        tensor = torch.from_numpy(samples)
        timeout = max(0.05, options.final_refinement_timeout_ms / 1000)
        started = perf_counter()
        with self._scheduler.slot("final", timeout=timeout) as acquired:
            if not acquired:
                return {**fallback, "reason": "stream_priority_timeout"}
            try:
                result = self.final_asr.generate(
                    input=[tensor],
                    batch_size=1,
                    language="中文",
                    itn=True,
                    hotwords=list(options.decoder_hotwords[:32]),
                    max_length=192,
                )
            except Exception as exc:  # noqa: BLE001 - final pass must never break streaming
                self.final_error = str(exc)
                return {**fallback, "reason": "inference_error", "error": str(exc)}
        text = _result_text(result)
        elapsed_ms = int((perf_counter() - started) * 1000)
        if not FunASRStreamSession._meaningful_text(text):
            return {
                **fallback,
                "reason": "empty_or_unreliable_result",
                "latency_ms": elapsed_ms,
            }
        return {
            **fallback,
            "applied": True,
            "reason": "ok",
            "text": text,
            "latency_ms": elapsed_ms,
        }

    def transcribe_audio(self, audio: bytes) -> dict[str, Any]:
        """Decode a complete reference clip and transcribe it as one utterance."""
        if not self.load():
            raise RuntimeError(self.error or "FunASR is unavailable")
        try:
            import numpy as np
            import soundfile as sf

            samples, sample_rate = sf.read(BytesIO(audio), dtype="float32", always_2d=True)
        except Exception as exc:  # noqa: BLE001 - returned as an actionable API error
            raise ValueError(f"无法解码参考音频：{exc}") from exc
        if sample_rate <= 0 or samples.size == 0:
            raise ValueError("参考音频为空或采样率无效")

        mono = samples.mean(axis=1)
        duration = float(len(mono) / sample_rate)
        if duration < 0.2:
            raise ValueError("参考音频过短，至少需要 0.2 秒")
        if duration > 120:
            raise ValueError("参考音频过长，请裁剪到 120 秒以内")

        target_rate = 16000
        if sample_rate != target_rate:
            target_length = max(1, int(round(len(mono) * target_rate / sample_rate)))
            source_points = np.arange(len(mono), dtype="float64")
            target_points = np.linspace(0, max(0, len(mono) - 1), target_length)
            mono = np.interp(target_points, source_points, mono).astype("float32")
        mono = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
        pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()

        # Treat the reference as one utterance even if it contains short pauses.
        session = FunASRStreamSession(
            self,
            ASRSessionOptions(
                sample_rate=target_rate,
                silence_ms=10_000_000,
                energy_threshold=0,
                auto_send=False,
            ),
        )
        events = session.feed(pcm)
        events.extend(session.feed(b"\x00\x00" * int(target_rate * 0.1), force_final=True))
        final_text = ""
        partial_text = ""
        for event in events:
            data = event.get("data") or {}
            if event.get("event") == "asr.partial":
                partial_text = str(data.get("text") or partial_text).strip()
            elif event.get("event") == "asr.final":
                final_text = str(data.get("text") or "").strip()
        text = final_text or partial_text
        if not text:
            raise ValueError("没有识别到清晰语音，请换用更干净的参考音频")
        return {
            "text": text,
            "duration": round(duration, 3),
            "sample_rate": target_rate,
        }


class FunASRStreamSession:
    def __init__(self, runtime: FunASRRuntime, options: ASRSessionOptions) -> None:
        self.runtime = runtime
        self.options = options
        self.asr_cache: dict[str, Any] = {}
        self.vad_cache: dict[str, Any] = {}
        self.pending = bytearray()
        self.transcript = ""
        self.speaking = False
        self.silence_ms = 0
        self.voiced_ms = 0
        self.candidate_silence_ms = 0
        self.tail_resume_ms = 0
        self.candidate_active = False
        self.pending_speech_start = False
        self.chunk_ms = 480
        self.corrector = ASRTextCorrector(options)
        self._pre_roll = bytearray()
        self._utterance_pcm = bytearray()
        self._last_finalized_pcm = b""
        self._utterance_playback_active = False
        self._utterance_playback_text = ""
        self._last_playback_active = False
        self._last_endpoint_reason = ""
        self._last_endpoint_silence_ms = options.silence_ms
        self._partial_texts: list[str] = []
        self._vad_confirmed = False
        self._barge_confirmed_sent = False

    @property
    def chunk_bytes(self) -> int:
        return int(self.options.sample_rate * (self.chunk_ms / 1000) * 2)

    def configure_playback(
        self,
        *,
        playing: bool,
        energy_threshold: float,
        min_speech_ms: int,
        candidate_release_ms: int,
        playback_text: str = "",
    ) -> bool:
        """Update live thresholds without discarding an in-flight candidate."""

        changed = self.options.playback_active != playing
        self.options.energy_threshold = energy_threshold
        self.options.min_speech_ms = min_speech_ms
        self.options.candidate_release_ms = candidate_release_ms
        self.options.playback_active = playing
        self.options.playback_text = playback_text[:4000] if playing else ""
        if changed and not self.speaking:
            self.voiced_ms = 0
            self.candidate_silence_ms = 0
            self.candidate_active = False
            self.pending_speech_start = False
        return changed

    def configure_input_gate(self, locked: bool) -> bool:
        """Atomically drop any unfinished utterance while the main turn is committed."""

        changed = self.options.input_locked != locked
        self.options.input_locked = locked
        if changed:
            self._reset_stream_state()
        return changed

    @staticmethod
    def _pcm_array(pcm: bytes) -> Any:
        import numpy as np

        return np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0

    @staticmethod
    def _energy(samples: Any) -> float:
        if getattr(samples, "size", 0) == 0:
            return 0.0
        return math.sqrt(float((samples * samples).mean()))

    def _tail_speech_like(self, samples: Any, energy: float) -> bool:
        """Reject weak broadband breath while preserving clear resumed speech."""

        if getattr(samples, "size", 0) < 2:
            return False
        strong_threshold = max(
            self.options.energy_threshold * self.options.tail_resume_energy_ratio,
            10 ** (-30 / 20),
        )
        if energy >= strong_threshold:
            return True
        signs = samples >= 0
        zero_crossing_rate = float((signs[1:] != signs[:-1]).mean())
        return energy >= self.options.energy_threshold and zero_crossing_rate <= 0.16

    def _endpoint_policy(self) -> tuple[int, str]:
        base = max(250, self.options.silence_ms)
        if not self.options.dynamic_endpointing:
            return base, "fixed_silence"
        if self.options.playback_active or self._utterance_playback_active:
            return max(base, 850), "playback_guard"
        text = self.transcript.rstrip()
        if re.search(r"[。！？!?]$", text):
            return min(base, 400), "sentence_terminal"
        compact = re.sub(r"[，。！？、,.!?\s]+$", "", text)
        if re.search(r"(?:嗯|呃|额|那个|就是|然后|怎么说|我想想)$", compact):
            return max(base, 900), "hesitation_tail"
        if not compact:
            return max(base, 650), "awaiting_first_text"
        return base, "normal_silence"

    def feed(self, pcm: bytes, *, force_final: bool = False) -> list[dict[str, Any]]:
        if self.options.input_locked:
            return []
        if not self.runtime.load():
            raise RuntimeError(self.runtime.error or "FunASR is unavailable")
        incoming = self._pcm_array(pcm)
        incoming_ms = int(len(incoming) / self.options.sample_rate * 1000)
        incoming_energy = self._energy(incoming)
        incoming_voiced = incoming_energy >= self.options.energy_threshold
        events: list[dict[str, Any]] = []
        candidate_cleared = False
        tracking_before = self.speaking or self.candidate_active
        if incoming_voiced or tracking_before:
            if not self._utterance_pcm and self._pre_roll:
                self._utterance_pcm.extend(self._pre_roll)
            self._pre_roll.clear()
            self._utterance_pcm.extend(pcm)
            maximum_bytes = self.options.sample_rate * 2 * 30
            if len(self._utterance_pcm) > maximum_bytes:
                del self._utterance_pcm[: len(self._utterance_pcm) - maximum_bytes]
            self._utterance_playback_active = (
                self._utterance_playback_active or self.options.playback_active
            )
            if self.options.playback_active and self.options.playback_text:
                self._utterance_playback_text = self.options.playback_text
        else:
            self._pre_roll.extend(pcm)
            maximum_pre_roll = int(self.options.sample_rate * 2 * 0.6)
            if len(self._pre_roll) > maximum_pre_roll:
                del self._pre_roll[: len(self._pre_roll) - maximum_pre_roll]
        tailing = self.speaking and self.silence_ms > 0
        tail_speech_like = incoming_voiced and self._tail_speech_like(incoming, incoming_energy)
        if incoming_voiced:
            if not self.speaking and not self.candidate_active:
                self.candidate_active = True
                events.append(
                    {
                        "event": "asr.speech_candidate",
                        "data": {
                            "energy": incoming_energy,
                            "energy_db": 20 * math.log10(max(incoming_energy, 1e-9)),
                            "playback_active": self.options.playback_active,
                        },
                    }
                )
            self.voiced_ms += incoming_ms
            self.candidate_silence_ms = 0
            if tailing:
                if tail_speech_like:
                    self.tail_resume_ms += incoming_ms
                    if self.tail_resume_ms >= self.options.tail_resume_min_ms:
                        self.silence_ms = 0
                        self.tail_resume_ms = 0
                else:
                    self.silence_ms += incoming_ms
                    self.tail_resume_ms = 0
            else:
                self.silence_ms = 0
                self.tail_resume_ms = 0
            if not self.speaking and self.voiced_ms >= self.options.min_speech_ms:
                if self.runtime.vad is not None:
                    self.pending_speech_start = True
                else:
                    self.speaking = True
                    events.append(
                        {
                            "event": "asr.speech_start",
                            "data": {
                                "energy": incoming_energy,
                                "energy_db": 20 * math.log10(max(incoming_energy, 1e-9)),
                                "playback_active": self.options.playback_active,
                                "confirmed_by": "energy_duration",
                            },
                        }
                    )
        elif self.speaking:
            self.silence_ms += incoming_ms
            self.tail_resume_ms = 0
        else:
            self.voiced_ms = 0
            if self.candidate_active:
                self.candidate_silence_ms += incoming_ms
                if self.candidate_silence_ms >= self.options.candidate_release_ms:
                    self.candidate_active = False
                    self.pending_speech_start = False
                    self.candidate_silence_ms = 0
                    candidate_cleared = True
                    events.append(
                        {
                            "event": "asr.speech_candidate_cleared",
                            "data": {"playback_active": self.options.playback_active},
                        }
                    )

        self.pending.extend(pcm)
        endpoint_silence_ms, endpoint_reason = self._endpoint_policy()
        silence_final = (
            self.speaking
            and self.silence_ms >= endpoint_silence_ms
            and self.tail_resume_ms == 0
        )
        while (
            len(self.pending) >= self.chunk_bytes
            or ((force_final or silence_final) and self.pending)
        ):
            length = (
                len(self.pending)
                if force_final or silence_final
                else self.chunk_bytes
            )
            raw = bytes(self.pending[:length])
            del self.pending[:length]
            samples = self._pcm_array(raw)
            is_final = force_final or silence_final
            with _runtime_slot(self.runtime, "stream"):
                vad_result: Any = None
                if self.runtime.vad is not None:
                    vad_result = self.runtime.vad.generate(
                        input=samples,
                        cache=self.vad_cache,
                        is_final=is_final,
                        chunk_size=200,
                    )
                generate_options: dict[str, Any] = {}
                if self.options.decoder_hotwords:
                    generate_options["hotword"] = list(self.options.decoder_hotwords)
                result = self.runtime.asr.generate(
                    input=samples,
                    cache=self.asr_cache,
                    is_final=is_final,
                    chunk_size=[0, 8, 4],
                    encoder_chunk_look_back=4,
                    decoder_chunk_look_back=1,
                    **generate_options,
                )
            text = _result_text(result)
            vad_confirmed = _vad_has_speech(vad_result)
            self._vad_confirmed = self._vad_confirmed or vad_confirmed
            # During playback the adaptive energy gate and FSMN-VAD already
            # provide two independent checks. Requiring a decoded text token as
            # a third check made real barge-in fail whenever the first streaming
            # ASR chunk had not formed characters yet.
            speech_confirmed = (
                vad_confirmed if self.runtime.vad is not None else bool(text)
            )
            if self.pending_speech_start and speech_confirmed:
                self.pending_speech_start = False
                self.speaking = True
                events.append(
                    {
                        "event": "asr.speech_start",
                        "data": {
                            "energy": incoming_energy,
                            "energy_db": 20 * math.log10(max(incoming_energy, 1e-9)),
                            "playback_active": self.options.playback_active,
                            "confirmed_by": (
                                "fsmn_vad+asr"
                                if self.options.playback_active and text
                                else "fsmn_vad" if vad_confirmed else "asr_partial"
                            ),
                        },
                    }
                )
            accept_text = bool(text) and (
                self.options.playback_active
                or self.speaking
                or vad_confirmed
                or self.runtime.vad is None
            )
            if accept_text:
                self.transcript += text
                self._partial_texts.append(self.transcript)
                self._partial_texts = self._partial_texts[-3:]
                events.append(
                    {"event": "asr.partial", "data": {"text": self.transcript, "delta": text}}
                )
                compact = _compact_speech_text(self.transcript)
                stable_partial = self._stable_partial()
                explicit_stop = compact in STOP_COMMANDS
                can_confirm_early = explicit_stop or (
                    stable_partial and len(compact) >= 4
                )
                if (
                    self.options.playback_active
                    and self._vad_confirmed
                    and can_confirm_early
                    and not self._barge_confirmed_sent
                    and not _looks_like_echo(self.transcript, self.options.playback_text)
                ):
                    self._barge_confirmed_sent = True
                    events.append(
                        {
                            "event": "asr.barge_in_confirmed",
                            "data": {
                                "confirmed_text": self.transcript,
                                "explicit_stop": explicit_stop,
                                "decision_reasons": [
                                    "vad_confirmed",
                                    "explicit_stop" if explicit_stop else "stable_partial",
                                ],
                            },
                        }
                    )
            if is_final:
                self._last_endpoint_reason = "manual_stop" if force_final else endpoint_reason
                self._last_endpoint_silence_ms = endpoint_silence_ms
                self._capture_finalized_audio()
                events.append(self._final_event("asr.final", auto_send=self.options.auto_send))
                self._reset_stream_state()
                break
        if (
            candidate_cleared
            and self.options.playback_active
            and self.options.deferred_during_playback
            and self._meaningful_text(self.transcript)
        ):
            self._last_endpoint_reason = "playback_candidate_deferred"
            self._last_endpoint_silence_ms = self.options.candidate_release_ms
            self._capture_finalized_audio()
            events.append(self._final_event("asr.deferred", auto_send=False))
            self._reset_stream_state()
        elif candidate_cleared and not self.speaking:
            self._reset_stream_state()
        return events

    @staticmethod
    def _meaningful_text(text: str) -> bool:
        compact = _compact_speech_text(text)
        return len(compact) >= 2 and compact not in FILLER_ONLY

    def _stable_partial(self) -> bool:
        if len(self._partial_texts) < 2:
            return False
        return _text_similarity(self._partial_texts[-2], self._partial_texts[-1]) >= 0.86

    def _final_event(self, name: str, *, auto_send: bool) -> dict[str, Any]:
        raw_text = self.transcript.strip()
        punctuated_text = raw_text
        if punctuated_text and self.runtime.punc is not None:
            with _runtime_slot(self.runtime, "stream"):
                punctuated = _result_text(self.runtime.punc.generate(input=punctuated_text))
            punctuated_text = punctuated or punctuated_text
        corrected_text, matches = self.corrector.apply(punctuated_text)
        event = {
            "event": name,
            "data": {
                "text": corrected_text,
                "raw_text": raw_text,
                "auto_send": auto_send,
                "correction_matches": matches,
                "vocabulary_revision": self.options.vocabulary_revision,
                "endpoint_reason": self._last_endpoint_reason,
                "endpoint_silence_ms": self._last_endpoint_silence_ms,
                "playback_active": self._utterance_playback_active,
                "playback_text": self._utterance_playback_text,
                "vad_confirmed": self._vad_confirmed,
                "voiced_ms": self.voiced_ms,
                "stable_partial": self._stable_partial(),
            },
        }
        return apply_asr_decision(event)

    def _capture_finalized_audio(self) -> None:
        self._last_finalized_pcm = bytes(self._utterance_pcm)
        self._last_playback_active = self._utterance_playback_active

    def pop_finalized_audio(self) -> tuple[bytes, bool]:
        value = self._last_finalized_pcm
        playback_active = self._last_playback_active
        self._last_finalized_pcm = b""
        self._last_playback_active = False
        return value, playback_active

    def _reset_stream_state(self) -> None:
        self.asr_cache = {}
        self.vad_cache = {}
        self.pending.clear()
        self.transcript = ""
        self.speaking = False
        self.silence_ms = 0
        self.voiced_ms = 0
        self.candidate_silence_ms = 0
        self.tail_resume_ms = 0
        self.candidate_active = False
        self.pending_speech_start = False
        self._pre_roll.clear()
        self._utterance_pcm.clear()
        self._utterance_playback_active = False
        self._utterance_playback_text = ""
        self._last_endpoint_reason = ""
        self._last_endpoint_silence_ms = self.options.silence_ms
        self._partial_texts.clear()
        self._vad_confirmed = False
        self._barge_confirmed_sent = False

    def reset(self) -> None:
        self._reset_stream_state()
        self._last_finalized_pcm = b""
        self._last_playback_active = False
