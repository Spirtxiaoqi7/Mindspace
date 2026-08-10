"""OpenAI-compatible adapter with real server-sent token streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator
from threading import local
from time import monotonic
from typing import Any

import httpx

from mindspace_graph.models import ApiConfig, ModelUsage


class EmptyVisibleContentError(ValueError):
    """The provider completed successfully but produced no user-visible text."""


def _text_content(value: Any) -> str:
    """Normalize string and OpenAI-compatible block-array content."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
            continue
        content = block.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)


class OpenAICompatibleLanguageModel:
    """Keep provider I/O behind a small, vendor-neutral streaming port."""

    timeout = httpx.Timeout(connect=15, read=120, write=30, pool=15)
    visible_timeout = httpx.Timeout(connect=10, read=15, write=20, pool=10)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._local = local()
        self._owns_client = client is None
        # Reuse one bounded pool, but retry TLS/connect establishment before the
        # provider has accepted a request. This covers transient VPN/TUN route
        # switches without issuing a second model call after output has started.
        self._client = client or httpx.Client(
            timeout=self.timeout,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=20.0,
            ),
            transport=httpx.HTTPTransport(retries=2),
            # Provider traffic should not silently inherit a stale HTTP proxy
            # variable from the launcher environment. System-level TUN/VPN
            # routing continues to work because it operates below httpx.
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def take_usage(self) -> ModelUsage | None:
        usage = getattr(self._local, "usage", None)
        self._local.usage = None
        return usage

    def _capture_usage(self, payload: dict[str, Any], config: ApiConfig, request_kind: str) -> None:
        raw = payload.get("usage")
        if not isinstance(raw, dict):
            return
        prompt_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
        cached = 0
        source = "unreported"
        if isinstance(prompt_details, dict) and prompt_details.get("cached_tokens") is not None:
            cached = max(0, int(prompt_details.get("cached_tokens") or 0))
            source = "prompt_tokens_details.cached_tokens"
        elif raw.get("prompt_cache_hit_tokens") is not None:
            cached = max(0, int(raw.get("prompt_cache_hit_tokens") or 0))
            source = "prompt_cache_hit_tokens"
        elif raw.get("cache_read_input_tokens") is not None:
            cached = max(0, int(raw.get("cache_read_input_tokens") or 0))
            source = "cache_read_input_tokens"
        prompt = max(0, int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0))
        completion = max(0, int(raw.get("completion_tokens") or raw.get("output_tokens") or 0))
        self._local.usage = ModelUsage(
            model=config.model,
            request_kind=request_kind,
            prompt_tokens=prompt,
            cached_tokens=min(cached, prompt) if prompt else cached,
            completion_tokens=completion,
            total_tokens=max(0, int(raw.get("total_tokens") or prompt + completion)),
            cache_source=source,
        )

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "".join(self.stream(messages, config))

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        return "".join(self.stream_repair(messages, raw_output, errors, config))

    def stream(self, messages: list[dict[str, str]], config: ApiConfig) -> Iterator[str]:
        self._local.usage = None
        yield from self._stream(messages, config, request_kind="generation")

    def stream_repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> Iterator[str]:
        prompt = (
            "保持既定角色与事实规则，修正下面输出的结构。只返回完整的 response 和 "
            "json_update，不要解释；必须先输出 <response>。\n"
            f"错误：{json.dumps(errors, ensure_ascii=False)}\n原输出：\n{raw_output}"
        )
        repair_messages = [*messages, {"role": "user", "content": prompt}]
        self._local.usage = None
        yield from self._stream(repair_messages, config, request_kind="repair")

    def compact(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        """Run a separate non-streaming low-priority context compaction request."""

        # DeepSeek V4 enables thinking by default. A bounded compaction request
        # can otherwise spend its entire budget on reasoning_content and return
        # an empty final content field. Reuse the structured-call compatibility
        # ladder so official DeepSeek gets non-thinking JSON while generic
        # OpenAI-compatible servers can still fall back to their base fields.
        return self._private_completion(
            messages,
            config,
            request_kind="compaction",
            max_tokens=config.max_tokens,
            timeout=self.timeout,
        )

    def audit_role(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        """Independent post-turn audit; callers schedule it after visible output."""

        return self._private_completion(
            messages,
            config,
            request_kind="role_audit",
            max_tokens=600,
            timeout=self.timeout,
        )

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str = "structured_generation",
        max_tokens: int = 1400,
        timeout_seconds: float = 30.0,
    ) -> str:
        """Generate compact JSON with thinking disabled and compatibility fallbacks."""

        seconds = max(1.0, min(180.0, float(timeout_seconds)))
        return self._private_completion(
            messages,
            config,
            request_kind=request_kind,
            max_tokens=max_tokens,
            timeout=httpx.Timeout(
                connect=min(5.0, seconds),
                read=seconds,
                write=min(5.0, seconds),
                pool=min(5.0, seconds),
            ),
        )

    def extract_memory(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str:
        """Extract a small state delta only on memory-worthy turns."""

        seconds = max(0.5, min(8.0, float(timeout_seconds)))
        return self._private_completion(
            messages,
            config,
            request_kind="memory_extract",
            max_tokens=700,
            timeout=httpx.Timeout(
                connect=min(3.0, seconds),
                read=seconds,
                write=min(3.0, seconds),
                pool=min(3.0, seconds),
            ),
        )

    def _private_completion(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str,
        max_tokens: int,
        timeout: httpx.Timeout,
    ) -> str:
        """私有结构化调用：不流式展示，并为不同 OpenAI 兼容服务逐级降级字段。"""

        self._local.usage = None
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        base_body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": min(max_tokens, config.max_tokens),
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        # DeepSeek V4 enables thinking by default. Small planners need visible
        # JSON, not a reasoning trace that consumes their entire output budget.
        # Retry progressively for generic OpenAI-compatible servers that reject
        # vendor fields or JSON mode.
        variants = [
            {
                **base_body,
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
            },
            {**base_body, "thinking": {"type": "disabled"}},
            base_body,
        ]
        last_error: Exception | None = None
        for body in variants:
            try:
                response = self._client.post(endpoint, headers=headers, json=body, timeout=timeout)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404, 422}:
                    raise
                last_error = exc
                continue
            payload = response.json()
            self._capture_usage(payload, config, request_kind)
            choices = payload.get("choices") or []
            choice = choices[0] if choices else {}
            finish_reason = str(choice.get("finish_reason") or "").strip().lower()
            content = _text_content((choice.get("message") or {}).get("content") if choice else None)
            if finish_reason in {"length", "max_tokens"}:
                raise ValueError("模型输出达到长度上限，JSON 在完成前被截断")
            if content.strip():
                return content
            last_error = ValueError(f"{request_kind} response content is blank")
        if last_error is not None:
            raise last_error
        raise ValueError(f"{request_kind} response content is blank")

    def _stream(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str,
    ) -> Iterator[str]:
        """Visible generation prefers non-thinking output and retries one blank result."""

        variants = [
            (True, True),
            (False, True),
            (False, False),
        ]
        last_error: Exception | None = None
        blank_retry_used = False
        for include_usage, disable_thinking in variants:
            try:
                yield from self._stream_with_connect_retry(
                    messages,
                    config,
                    request_kind=request_kind,
                    include_usage=include_usage,
                    disable_thinking=disable_thinking,
                )
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404, 422}:
                    raise
                # Compatibility fields were rejected before any visible token.
                last_error = exc
                continue
            except EmptyVisibleContentError as exc:
                last_error = exc
                if blank_retry_used:
                    raise
                blank_retry_used = True
                # DeepSeek documents occasional empty final content. Retry once,
                # still in non-thinking mode and before the UI saw any text.
                yield from self._stream_with_connect_retry(
                    messages,
                    config,
                    request_kind=request_kind,
                    include_usage=False,
                    disable_thinking=disable_thinking,
                )
                return
        if last_error is not None:
            raise last_error
        raise EmptyVisibleContentError(f"{request_kind} response content is blank")

    def _stream_with_connect_retry(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str,
        include_usage: bool,
        disable_thinking: bool,
    ) -> Iterator[str]:
        """Retry one failed connection or silent first response before visible text."""

        emitted = False
        for attempt in range(2):
            try:
                for chunk in self._stream_once(
                    messages,
                    config,
                    request_kind=request_kind,
                    include_usage=include_usage,
                    disable_thinking=disable_thinking,
                ):
                    emitted = True
                    yield chunk
                return
            except (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ):
                if emitted or attempt > 0:
                    raise
                # A failed handshake, incomplete chunked body, or connection
                # that never produced its first event gets one fresh request.
                # Once text is visible, never retry and risk duplicating a
                # partial answer.
                continue

    def _stream_once(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str,
        include_usage: bool,
        disable_thinking: bool,
    ) -> Iterator[str]:
        """发送真实 provider 请求并把 SSE delta.content 原样交给协议解析层。"""

        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        }
        if include_usage:
            body["stream_options"] = {"include_usage": True}
        if disable_thinking:
            body["thinking"] = {"type": "disabled"}
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        saw_content = False
        saw_reasoning = False
        finish_reason = ""
        started_at = monotonic()
        with self._client.stream(
            "POST", endpoint, headers=headers, json=body, timeout=self.visible_timeout
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                payload = json.loads(data)
                self._capture_usage(payload, config, request_kind)
                choices = payload.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                reasoning = _text_content(delta.get("reasoning_content"))
                if reasoning:
                    saw_reasoning = True
                content = _text_content(delta.get("content"))
                if content:
                    saw_content = True
                    yield content
                elif monotonic() - started_at >= 12:
                    detail = "reasoning-only" if saw_reasoning else "no visible token"
                    raise EmptyVisibleContentError(f"{request_kind} first visible token timeout ({detail})")
                if choice.get("finish_reason") is not None:
                    finish_reason = str(choice.get("finish_reason") or "")
        if not saw_content:
            detail = "reasoning-only" if saw_reasoning else "empty"
            if finish_reason:
                detail += f", finish_reason={finish_reason}"
            raise EmptyVisibleContentError(f"{request_kind} response content is blank ({detail})")

