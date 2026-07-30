"""Deterministic voice-render metadata for streamed companion replies.

The metadata is intentionally tiny and transport-only.  It lets one LLM call
choose a Qwen3-TTS prosody preset without allowing presentation hints to leak
into the visible reply, session history, or retrieval corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Delivery presets only: CustomVoice's selected speaker remains fixed.  The
# finite library gives the model useful range without accepting arbitrary TTS
# instructions that could leak into speech or destabilize the voice identity.
VOICE_CUES = frozenset({
    "neutral", "thoughtful", "warm", "firm", "playful", "intimate",
    "reflective", "tender", "teasing", "lively", "dramatic", "breathy",
    "laughing", "sighing", "seductive", "alluring", "moaning", "satisfied",
})
ADULT_VOICE_CUES = frozenset({"seductive", "alluring", "moaning", "satisfied"})
DEFAULT_VOICE_CUE = "neutral"
_VOICE_CUE = re.compile(r"^\s*\[\[voice:([a-z_]+)\]\]\s*", flags=re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"([。！？!?])(?![.…])(?=\S)")
_CLAUSE_BOUNDARY = re.compile(r"([，；;])(?=\S)")


# Qwen's CustomVoice ``instruct`` is an optional style hint, not a second
# system prompt.  Long lists of identity constraints and negative directions
# made the model sound as if it were following a dubbing director.  Keep these
# hints short and positive.  The neutral hint deliberately matches the
# natural-conversation instruction used by the original direct-Qwen sample:
# without it, CustomVoice tends to fall back to a clean but announcer-like
# delivery even when the visible text is colloquial.
_QWEN3_STYLE_INSTRUCTIONS = {
    "neutral": "像熟悉伴侣近距离聊天，句间自然换气，偶尔带一点很轻的笑意。",
    "thoughtful": "边想边说，语速稍慢，停顿自然。",
    "warm": "像和熟悉伴侣随口聊天，语气温和放松，句间自然换气。",
    "firm": "语气稳定直接，重点清楚，语速正常。",
    "playful": "像在逗熟人，带一点轻松笑意，语速正常。",
    "intimate": "贴近、轻声、放松地说，停顿时能听见轻微换气。",
    "reflective": "像边回想边说，允许一次轻微犹豫。",
    "tender": "柔和、耐心、放松地说。",
    "teasing": "像熟人闲聊，语速正常，只有很轻的调侃。",
    "lively": "轻快自然，重音有少量变化。",
    "dramatic": "在情绪峰值放慢并加强一次重音。",
    "breathy": "贴近轻声说，句间能听见少量自然换气。",
    "laughing": "在合适处带一次短促、很轻的笑声，再自然接着说。",
    "sighing": "先轻叹一次，再自然接着说。",
    "seductive": "贴近低声、从容地说，语速略慢，带少量气息感。",
    "alluring": "靠近耳边般低声诱哄，重音少而轻，句间自然换气。",
    "moaning": "说话中自然带出一两次短促轻哼或呼气。",
    "satisfied": "舒缓满足地低声说，带一次很轻的呼气。",
}


def normalize_voice_cue(value: str | None, *, allow_adult: bool = True) -> str:
    cue = str(value or "").strip().lower()
    if cue not in VOICE_CUES:
        return DEFAULT_VOICE_CUE
    return cue if allow_adult or cue not in ADULT_VOICE_CUES else DEFAULT_VOICE_CUE


def extract_voice_cue(value: str, *, allow_adult: bool = True) -> tuple[str, str, bool]:
    """Return ``(cue, spoken_text, explicit_tag)`` for a completed reply."""

    match = _VOICE_CUE.match(value or "")
    if match is None:
        return DEFAULT_VOICE_CUE, value, False
    cue = normalize_voice_cue(match.group(1), allow_adult=allow_adult)
    return cue, (value or "")[match.end() :].lstrip(), True


def infer_qwen3_voice_cue(text: str, requested_cue: str | None) -> str:
    """Resolve one whole-reply style without changing the fixed speaker."""

    cue = normalize_voice_cue(requested_cue)
    if cue != DEFAULT_VOICE_CUE:
        return cue
    spoken = str(text or "")
    if re.search(r"(?:呵|哈){1,3}[……，。]?", spoken):
        return "laughing"
    if re.search(r"(?:呼|唔)[……，。]?", spoken):
        return "breathy"
    if re.search(r"(?:唉|哎)[……，。]?", spoken):
        return "sighing"
    return cue


def qwen3_instructions(cue: str, *, speed: float = 1.0) -> str:
    """Return one concise CustomVoice style hint.

    Speaker identity is already fixed by the request's ``voice`` field.  Text
    cleanup and output-format rules belong upstream and must not be repeated in
    the acoustic instruction.
    """

    requested_speed = max(0.5, min(2.0, float(speed)))
    pace = (
        "语速舒缓偏慢，"
        if requested_speed <= 0.92
        else "语速稍慢，"
        if requested_speed < 0.99
        else "语速稍快，"
        if requested_speed > 1.05
        else ""
    )
    return f"{pace}{_QWEN3_STYLE_INSTRUCTIONS[normalize_voice_cue(cue)]}"


def pace_qwen3_base_text(value: str, *, speed: float = 1.0) -> str:
    """Add audible semantic pauses for Base voice-clone playback.

    Qwen3-TTS Base does not expose CustomVoice's instruction control and the
    vLLM streaming endpoint does not implement acoustic time stretching.
    Slower playback therefore uses punctuation that the model understands
    natively. The visible assistant reply is left untouched.
    """

    text = re.sub(r"\s*\n+\s*", "……", str(value or "").strip())
    if not text:
        return ""
    requested_speed = max(0.5, min(2.0, float(speed)))
    if requested_speed >= 0.99:
        return text

    text = _SENTENCE_BOUNDARY.sub(r"\1……", text)
    if requested_speed <= 0.92 and "……" not in value:
        # Add one breathing point inside a longer answer, not after every
        # comma. This slows the cadence without making it sound fragmented.
        text = _CLAUSE_BOUNDARY.sub(r"\1……", text, count=1)
    return text


@dataclass(slots=True)
class VoiceCueStream:
    """Hold a possibly token-split leading cue until it can be decided safely."""

    allow_adult: bool = True
    cue: str = DEFAULT_VOICE_CUE
    resolved: bool = False
    explicit_tag: bool = False
    _buffer: str = ""

    def feed(self, value: str) -> list[str]:
        if not value:
            return []
        if self.resolved:
            return [value]
        self._buffer += value
        match = _VOICE_CUE.match(self._buffer)
        if match is not None:
            self.cue = normalize_voice_cue(match.group(1), allow_adult=self.allow_adult)
            self.explicit_tag = True
            self.resolved = True
            remaining = self._buffer[match.end() :]
            self._buffer = ""
            return [remaining] if remaining else []

        # Before the first non-whitespace characters arrive, retain a possible
        # split tag.  Once it cannot be one, preserve normal low-latency text.
        probe = self._buffer.lstrip().lower()
        if "[[voice:".startswith(probe) or not probe:
            return []
        self.resolved = True
        emitted = self._buffer
        self._buffer = ""
        return [emitted]

    def flush(self) -> list[str]:
        if self.resolved:
            return []
        self.resolved = True
        emitted = self._buffer
        self._buffer = ""
        return [emitted] if emitted else []
