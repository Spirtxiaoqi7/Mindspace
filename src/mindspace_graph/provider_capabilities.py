"""Provider-neutral capability discovery for OpenAI-compatible endpoints."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from threading import RLock
from time import monotonic


class ProviderCapabilityState(StrEnum):
    UNKNOWN = "unknown"
    PROBING = "probing"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    TRANSIENT_FAILURE = "transient_failure"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    state: ProviderCapabilityState = ProviderCapabilityState.UNKNOWN
    protocol: str = "tools"
    updated_at: float = 0.0
    reason: str = ""


def provider_key(base_url: str, model: str) -> str:
    return f"{(base_url or '').strip().lower().rstrip('/')}\n{(model or '').strip().lower()}"


class ProviderCapabilityRegistry:
    """A process-local cache keyed only by endpoint and model, never by brand."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[str, ProviderCapabilities] = {}

    def get(self, base_url: str, model: str) -> ProviderCapabilities:
        with self._lock:
            return self._values.get(provider_key(base_url, model), ProviderCapabilities())

    def begin_probe(self, base_url: str, model: str) -> ProviderCapabilities:
        key = provider_key(base_url, model)
        with self._lock:
            current = self._values.get(key, ProviderCapabilities())
            if current.state in {ProviderCapabilityState.SUPPORTED, ProviderCapabilityState.UNSUPPORTED}:
                return current
            value = replace(current, state=ProviderCapabilityState.PROBING, updated_at=monotonic())
            self._values[key] = value
            return value

    def supported(self, base_url: str, model: str, *, protocol: str) -> None:
        self._set(base_url, model, ProviderCapabilityState.SUPPORTED, protocol, "")

    def unsupported(self, base_url: str, model: str, reason: str) -> None:
        self._set(base_url, model, ProviderCapabilityState.UNSUPPORTED, "none", reason)

    def transient_failure(self, base_url: str, model: str, reason: str) -> None:
        current = self.get(base_url, model)
        self._set(
            base_url,
            model,
            ProviderCapabilityState.TRANSIENT_FAILURE,
            current.protocol if current.protocol != "none" else "tools",
            reason,
        )

    def reset(self, base_url: str, model: str) -> None:
        with self._lock:
            self._values.pop(provider_key(base_url, model), None)

    def _set(
        self,
        base_url: str,
        model: str,
        state: ProviderCapabilityState,
        protocol: str,
        reason: str,
    ) -> None:
        with self._lock:
            self._values[provider_key(base_url, model)] = ProviderCapabilities(
                state=state,
                protocol=protocol,
                updated_at=monotonic(),
                reason=reason[:500],
            )


PROVIDER_CAPABILITIES = ProviderCapabilityRegistry()


def explicitly_rejects_tools(status_code: int, response_text: str) -> bool:
    """Only stable schema/protocol rejections may disable native tools."""

    if status_code not in {400, 404, 405, 415, 422}:
        return False
    text = (response_text or "").lower()
    if "thinking mode" in text and "tool_choice" in text:
        return False
    field = any(token in text for token in ("tool_choice", "tool_calls", "tools", "function_call", "functions"))
    rejection = any(
        token in text
        for token in (
            "not supported",
            "unsupported",
            "unknown field",
            "unknown parameter",
            "unrecognized",
            "extra inputs are not permitted",
            "invalid request",
        )
    )
    return field and rejection


class ProviderToolsUnsupportedError(RuntimeError):
    """The endpoint explicitly rejected both structured tool protocols."""
