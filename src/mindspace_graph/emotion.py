"""Serializable emotion contracts retained while the capability is disabled.

This module deliberately contains no model runtime, audio feature extraction,
HTTP calls, executors, or persistence.  See ``EmotionPort`` for the provider
boundary and ``DisabledEmotionCoordinator`` for the active implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AudioQuality(BaseModel):
    snr_db: float = 0.0
    voiced_ratio: float = Field(default=0.0, ge=0, le=1)
    clipping_ratio: float = Field(default=0.0, ge=0, le=1)
    echo_risk: float = Field(default=0.0, ge=0, le=1)
    usable: bool = False


class TextEmotionState(BaseModel):
    valence: float = Field(default=0.0, ge=-1, le=1)
    arousal: float = Field(default=0.0, ge=-1, le=1)
    dominance: float = Field(default=0.0, ge=-1, le=1)
    intent: str = Field(default="", max_length=100)
    needs: list[str] = Field(default_factory=list, max_length=5)
    emotion_distribution: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0, le=1)


class ResponseGuidance(BaseModel):
    warmth: float = Field(default=0.5, ge=0, le=1)
    directness: float = Field(default=0.5, ge=0, le=1)
    pace: str = "normal"
    avoid: list[str] = Field(default_factory=list)


class FusedEmotionState(BaseModel):
    valence: float = Field(default=0.0, ge=-1, le=1)
    arousal: float = Field(default=0.0, ge=-1, le=1)
    dominance: float = Field(default=0.0, ge=-1, le=1)
    emotion_distribution: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0, le=1)
    agreement: float = Field(default=0.0, ge=0, le=1)
    conflicts: list[str] = Field(default_factory=list)
    response_guidance: ResponseGuidance = Field(default_factory=ResponseGuidance)


class EmotionState(BaseModel):
    version: str = "1.0"
    turn_id: str
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    window_ms: int = Field(default=0, ge=0)
    quality: AudioQuality = Field(default_factory=AudioQuality)
    acoustic: dict[str, Any] = Field(default_factory=dict)
    text: TextEmotionState | None = None
    fusion: FusedEmotionState
    persistence: str = "ephemeral_voice_turn"
    eligible_for_json_evidence: bool = False
