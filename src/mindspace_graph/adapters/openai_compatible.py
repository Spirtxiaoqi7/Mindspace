"""OpenAI-compatible adapter with real server-sent token streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator
from threading import local
from typing import Any

import httpx

from mindspace_graph.models import ApiConfig, ModelUsage


class OpenAICompatibleLanguageModel:
    """Keep provider I/O behind a small, vendor-neutral streaming port."""

    timeout = httpx.Timeout(connect=15, read=120, write=30, pool=15)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._local = local()
        self._owns_client = client is None
        # 单个适配器复用连接池；不要为每个节点或每个 token 创建新 Client。
        self._client = client or httpx.Client(
            timeout=self.timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
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

        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": config.max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        response = self._client.post(
            endpoint, headers=headers, json=body, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        self._capture_usage(payload, config, "compaction")
        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("compaction response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("compaction response content is blank")
        return content

    def audit_role(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        """Independent post-turn audit; callers schedule it after visible output."""

        self._local.usage = None
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": min(600, config.max_tokens),
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        response = self._client.post(
            endpoint, headers=headers, json=body, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        self._capture_usage(payload, config, "role_audit")
        choices = payload.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("role audit response content is blank")
        return content

    def plan_capabilities(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        """Run a small private decision request before the user-visible response."""

        return self._private_completion(
            messages,
            config,
            request_kind="capability_plan",
            max_tokens=320,
            timeout=self.timeout,
        )

    def preflight(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str:
        """Run private structured planning with a caller-owned deadline."""

        seconds = max(0.3, min(15.0, float(timeout_seconds)))
        return self._private_completion(
            messages,
            config,
            request_kind="preflight",
            max_tokens=320,
            timeout=httpx.Timeout(
                connect=min(3.0, seconds),
                read=seconds,
                write=min(3.0, seconds),
                pool=min(3.0, seconds),
            ),
        )

    def review_research(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str:
        """Review first-pass evidence and optionally request one follow-up wave."""

        seconds = max(0.5, min(12.0, float(timeout_seconds)))
        return self._private_completion(
            messages,
            config,
            request_kind="research_review",
            max_tokens=520,
            timeout=httpx.Timeout(
                connect=min(3.0, seconds),
                read=seconds,
                write=min(3.0, seconds),
                pool=min(3.0, seconds),
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
                response = self._client.post(
                    endpoint, headers=headers, json=body, timeout=timeout
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404, 422}:
                    raise
                last_error = exc
                continue
            payload = response.json()
            self._capture_usage(payload, config, request_kind)
            choices = payload.get("choices") or []
            content = (choices[0].get("message") or {}).get("content") if choices else None
            if isinstance(content, str) and content.strip():
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
        """主生成/修复的统一流入口；仅在尚未产出 token 时降级 stream_options。"""

        try:
            yield from self._stream_once(
                messages, config, request_kind=request_kind, include_usage=True
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {400, 404, 422}:
                raise
            # Older compatible endpoints reject stream_options. Retrying before
            # any response token preserves output while marking usage unreported.
            yield from self._stream_once(
                messages, config, request_kind=request_kind, include_usage=False
            )

    def _stream_once(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        request_kind: str,
        include_usage: bool,
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
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        with self._client.stream(
            "POST", endpoint, headers=headers, json=body, timeout=self.timeout
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                payload = json.loads(data)
                self._capture_usage(payload, config, request_kind)
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content
