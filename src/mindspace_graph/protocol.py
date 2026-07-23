"""Parser for the four-block protocol already used by Mindspace."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from mindspace_graph.models import JsonUpdatePlan, ProtocolOutput

TRAILING_MODEL_TOKEN = re.compile(
    r"(?:<\|(?:im_end|endoftext)\|>|</s>)\s*$",
    flags=re.IGNORECASE,
)


def _clean_response_text(value: str) -> str:
    cleaned = value.rstrip()
    while TRAILING_MODEL_TOKEN.search(cleaned):
        cleaned = TRAILING_MODEL_TOKEN.sub("", cleaned).rstrip()
    return cleaned


class IncrementalResponseParser:
    """Extract only response-body deltas from arbitrarily split model chunks."""

    OPEN = re.compile(r"<response\b[^>]*>", flags=re.IGNORECASE)
    CLOSE = "</response>"
    FALLBACK_BLOCK = re.compile(r"<json_update\b[^>]*>", flags=re.IGNORECASE)
    TAIL_GUARD = len(CLOSE) + len("<|endoftext|>")
    PLAIN_STOP_MARKERS = (
        "<json_update",
        "</response>",
        "<|im_end|>",
        "<|endoftext|>",
        "</s>",
    )

    def __init__(self) -> None:
        self._buffer = ""
        self._opened = False
        self._closed = False
        self._plain = False

    @classmethod
    def _plain_tail_length(cls, value: str) -> int:
        """Retain only a possible split protocol marker, not ordinary reply text."""

        lowered = value.lower()
        retained = 0
        for marker in cls.PLAIN_STOP_MARKERS:
            for size in range(1, min(len(marker), len(lowered) + 1)):
                if lowered.endswith(marker[:size]):
                    retained = max(retained, size)
        return retained

    def _feed_plain(self) -> list[str]:
        lowered = self._buffer.lower()
        stops = [lowered.find(marker) for marker in self.PLAIN_STOP_MARKERS]
        stops = [index for index in stops if index >= 0]
        if stops:
            stop_at = min(stops)
            delta = _clean_response_text(self._buffer[:stop_at])
            self._buffer = self._buffer[stop_at:]
            self._closed = True
            return [delta] if delta else []

        retained = self._plain_tail_length(self._buffer)
        safe_length = len(self._buffer) - retained
        if safe_length <= 0:
            return []
        delta = self._buffer[:safe_length]
        self._buffer = self._buffer[safe_length:]
        return [delta] if delta else []

    def feed(self, chunk: str) -> list[str]:
        if not chunk or self._closed:
            return []
        self._buffer += chunk
        if self._plain:
            return self._feed_plain()
        if not self._opened:
            match = self.OPEN.search(self._buffer)
            if match is None:
                fallback = self.FALLBACK_BLOCK.search(self._buffer)
                if fallback is not None:
                    delta = _clean_response_text(self._buffer[: fallback.start()].strip())
                    delta = re.sub(r"</response>\s*$", "", delta, flags=re.IGNORECASE).strip()
                    self._buffer = self._buffer[fallback.start() :]
                    self._closed = True
                    return [delta] if delta else []
                # A transport chunk may end halfway through an opening protocol
                # marker (for example ``prefix<res``).  Hold just that ambiguous
                # startup chunk so the marker can be recognized on the next feed;
                # ordinary natural-language replies still stream immediately.
                last_angle = self._buffer.rfind("<")
                if last_angle >= 0:
                    suffix = self._buffer[last_angle:].lower()
                    if any(marker.startswith(suffix) for marker in ("<response", "<json_update")):
                        return []
                probe = self._buffer.lstrip().lower()
                if probe and not probe.startswith("<"):
                    self._opened = True
                    self._plain = True
                    return self._feed_plain()
                return []
            self._opened = True
            self._buffer = self._buffer[match.end() :]

        close_at = self._buffer.lower().find(self.CLOSE)
        if close_at >= 0:
            delta = _clean_response_text(self._buffer[:close_at])
            self._buffer = self._buffer[close_at + len(self.CLOSE) :]
            self._closed = True
            return [delta] if delta else []

        safe_length = max(0, len(self._buffer) - self.TAIL_GUARD + 1)
        if safe_length == 0:
            return []
        delta = self._buffer[:safe_length]
        self._buffer = self._buffer[safe_length:]
        return [delta] if delta else []

    @property
    def complete(self) -> bool:
        return self._closed


class ProtocolParser:
    """Convert untrusted model text into a validated response and JSON update plan."""

    TAGS = ("response", "json_update")

    def parse(self, raw: str) -> tuple[ProtocolOutput | None, list[str]]:
        errors: list[str] = []
        blocks = {tag: self._extract(raw, tag) for tag in self.TAGS}
        if blocks["response"] is None:
            blocks["response"] = self.response_text(raw)
        missing = [tag for tag, value in blocks.items() if value is None]
        if missing:
            return None, [f"missing protocol block: {tag}" for tag in missing]

        try:
            json_update = JsonUpdatePlan.model_validate(self._json(blocks["json_update"]))
        except (ValueError, ValidationError) as exc:
            errors.append(f"json_update invalid: {exc}")
            return None, errors

        response = _clean_response_text((blocks["response"] or "").strip())
        if not response:
            errors.append("response is blank")
            return None, errors

        if errors:
            return None, errors
        return (
            ProtocolOutput(
                response=response,
                json_update=json_update,
            ),
            [],
        )

    @staticmethod
    def _extract(raw: str, tag: str) -> str | None:
        match = re.search(
            rf"<{tag}\b[^>]*>(.*?)</{tag}>",
            raw or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _leading_response(raw: str) -> str | None:
        marker = re.search(
            r"<json_update\b[^>]*>",
            raw or "",
            flags=re.IGNORECASE,
        )
        if marker is None:
            return None
        value = (raw or "")[: marker.start()].strip()
        value = re.sub(r"</response>\s*$", "", value, flags=re.IGNORECASE).strip()
        return value or None

    @classmethod
    def response_text(cls, raw: str) -> str | None:
        tagged = cls._extract(raw, "response")
        if tagged:
            return _clean_response_text(tagged)
        leading = cls._leading_response(raw)
        if leading:
            return _clean_response_text(leading)
        value = (raw or "").strip()
        if value and not value.startswith(("<", "{", "[")):
            value = re.sub(r"</response>\s*$", "", value, flags=re.IGNORECASE).strip()
            return _clean_response_text(value)
        return None

    @staticmethod
    def _json(value: str | None) -> dict[str, Any]:
        if value is None:
            raise ValueError("missing JSON")
        cleaned = value.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end >= start:
            cleaned = cleaned[start : end + 1]
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("JSON block must be an object")
        return parsed
