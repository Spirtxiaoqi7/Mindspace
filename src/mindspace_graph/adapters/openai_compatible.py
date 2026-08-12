"""OpenAI-compatible adapter with real server-sent token streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator
from threading import local
from time import monotonic
from typing import Any

import httpx

from mindspace_graph.models import ApiConfig, ModelUsage, ProviderHttpAttempt
from mindspace_graph.provider_capabilities import PROVIDER_CAPABILITIES, explicitly_rejects_tools


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
            transport=httpx.HTTPTransport(retries=0),
            # Provider traffic should not silently inherit a stale HTTP proxy
            # variable from the launcher environment. System-level TUN/VPN
            # routing continues to work because it operates below httpx.
            trust_env=False,
        )
        self._tool_capabilities = PROVIDER_CAPABILITIES

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def take_usage(self) -> ModelUsage | None:
        usage = getattr(self._local, "usage", None)
        self._local.usage = None
        return usage

    def _reset_provider_attempts(self) -> None:
        self._local.provider_attempts = []

    def take_provider_attempts(self) -> list[ProviderHttpAttempt]:
        attempts = list(getattr(self._local, "provider_attempts", []))
        self._local.provider_attempts = []
        return attempts

    def _begin_provider_attempt(
        self,
        request_kind: str,
        *,
        compatibility_variant: str,
        retry_reason: str = "",
    ) -> tuple[int, float]:
        attempts = getattr(self._local, "provider_attempts", None)
        if not isinstance(attempts, list):
            attempts = []
            self._local.provider_attempts = attempts
        return len(attempts) + 1, monotonic()

    def _finish_provider_attempt(
        self,
        token: tuple[int, float],
        request_kind: str,
        *,
        status: str,
        compatibility_variant: str,
        retry_reason: str = "",
        http_status: int | None = None,
        error: str = "",
    ) -> None:
        attempt, started = token
        attempts = getattr(self._local, "provider_attempts", None)
        if not isinstance(attempts, list):
            attempts = []
            self._local.provider_attempts = attempts
        attempts.append(
            ProviderHttpAttempt(
                attempt=attempt,
                request_kind=request_kind,
                status=status,
                elapsed_ms=round((monotonic() - started) * 1000, 1),
                http_status=http_status,
                compatibility_variant=compatibility_variant,
                retry_reason=retry_reason,
                error=error[:500],
            )
        )

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
            requested_max_tokens=max(0, int(config.max_tokens)),
            finish_reason=str(getattr(self._local, "finish_reason", "") or ""),
        )

    def _capture_finish_reason(self, value: object) -> None:
        finish_reason = str(value or "").strip().lower()
        self._local.finish_reason = finish_reason
        usage = getattr(self._local, "usage", None)
        if usage is not None:
            self._local.usage = usage.model_copy(update={"finish_reason": finish_reason})

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
        self._local.native_tool_call = None
        self._reset_provider_attempts()
        yield from self._stream(messages, config, request_kind="generation")

    @staticmethod
    def _apply_output_token_budget(body: dict[str, Any], config: ApiConfig, max_tokens: int) -> None:
        """Map Mindspace's provider-neutral output budget to the wire field."""

        base_url = str(config.base_url or "").lower()
        model = str(config.model or "").lower()
        current_openai_model = (
            model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4")
        )
        field = "max_completion_tokens" if "api.openai.com" in base_url and current_openai_model else "max_tokens"
        body[field] = max(64, int(max_tokens))

    def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        config: ApiConfig,
        *,
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> Iterator[str]:
        """Stream visible text or retain one provider-native tool call."""

        self._local.usage = None
        self._local.native_tool_call = None
        self._reset_provider_attempts()
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "stream": True,
            "tools": tools,
        }
        self._apply_output_token_budget(body, config, config.max_tokens)
        # `auto` is the protocol default when tools are present. DeepSeek V4
        # rejects forced tool choices in thinking mode, while omitting the
        # default remains compatible with generic OpenAI-style endpoints.
        if tool_choice != "auto":
            body["tool_choice"] = tool_choice
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        calls: dict[int, dict[str, Any]] = {}
        visible = False
        last_usage_event: dict[str, Any] | None = None
        self._tool_capabilities.begin_probe(config.base_url, config.model)
        attempt = self._begin_provider_attempt("generation", compatibility_variant="chat_completions_tools")
        response_status: int | None = None
        try:
            with self._client.stream(
                "POST",
                endpoint,
                headers=headers,
                json=body,
                timeout=self.visible_timeout,
            ) as response:
                response_status = response.status_code
                if response.is_error:
                    response.read()
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    event = json.loads(payload)
                    if isinstance(event.get("usage"), dict):
                        last_usage_event = event
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    for part in delta.get("tool_calls") or []:
                        index = int(part.get("index", 0))
                        current = calls.setdefault(
                            index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if part.get("id"):
                            current["id"] += str(part["id"])
                        function = part.get("function") or {}
                        if function.get("name"):
                            current["function"]["name"] += str(function["name"])
                        if function.get("arguments"):
                            current["function"]["arguments"] += str(function["arguments"])
                    content = _text_content(delta.get("content"))
                    if content:
                        visible = True
                        yield content
            if not calls and not visible:
                raise EmptyVisibleContentError("model returned no visible content or tool call")
        except httpx.HTTPStatusError as exc:
            if explicitly_rejects_tools(exc.response.status_code, exc.response.text):
                self._tool_capabilities.unsupported(config.base_url, config.model, "unsupported_field")
            else:
                self._tool_capabilities.transient_failure(
                    config.base_url, config.model, f"http_{exc.response.status_code}"
                )
            self._finish_provider_attempt(
                attempt,
                "generation",
                status="http_error",
                compatibility_variant="native_tools",
                http_status=exc.response.status_code,
                error=str(exc),
            )
            raise
        except EmptyVisibleContentError as exc:
            self._finish_provider_attempt(
                attempt,
                "generation",
                status="empty",
                compatibility_variant="native_tools",
                http_status=response_status,
                error=str(exc),
            )
            raise
        except (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            self._finish_provider_attempt(
                attempt,
                "generation",
                status="transport_error",
                compatibility_variant="native_tools",
                error=str(exc),
            )
            raise
        except Exception as exc:
            self._finish_provider_attempt(
                attempt,
                "generation",
                status="error",
                compatibility_variant="native_tools",
                http_status=response_status,
                error=str(exc),
            )
            raise
        else:
            self._tool_capabilities.supported(config.base_url, config.model, protocol="tools")
            self._finish_provider_attempt(
                attempt,
                "generation",
                status="success",
                compatibility_variant="native_tools",
                http_status=response_status,
            )
        if last_usage_event is not None:
            self._capture_usage(last_usage_event, config, "generation")
        if len(calls) > 1:
            configured_names = {
                str((item.get("function") or {}).get("name") or "") for item in tools if isinstance(item, dict)
            } - {""}
            returned_names = {
                str((item.get("function") or {}).get("name") or "") for item in calls.values() if isinstance(item, dict)
            } - {""}
            selected_name = (
                str((tool_choice.get("function") or {}).get("name") or "") if isinstance(tool_choice, dict) else ""
            )
            forced_single_function = (
                len(configured_names) == 1 and returned_names == configured_names and selected_name in configured_names
            )
            if not forced_single_function:
                raise ValueError("provider returned more than one tool call in a single turn")
        if calls:
            self._local.native_tool_call = calls[min(calls)]
            return

    def take_native_tool_call(self) -> dict[str, Any] | None:
        call = getattr(self._local, "native_tool_call", None)
        self._local.native_tool_call = None
        return call if isinstance(call, dict) else None

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
        self._reset_provider_attempts()
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
        self._reset_provider_attempts()
        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        base_body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
        }
        self._apply_output_token_budget(base_body, config, min(max_tokens, config.max_tokens))
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        is_official_deepseek_v4 = (
            "api.deepseek.com" in str(config.base_url or "").lower()
            and str(config.model or "").lower().startswith("deepseek-v4-")
        )
        is_destiny_generation = request_kind in {
            "destiny_archetypes",
            "destiny_cards",
            "destiny_synthesis",
        }
        json_body = {**base_body, "response_format": {"type": "json_object"}}
        # DeepSeek V4 defaults to thinking mode. For every Destiny generation
        # stage the response is compact JSON, so hidden reasoning only steals
        # output from the JSON document and can leave it truncated. Keep the
        # provider-specific field off generic OpenAI-compatible endpoints, and
        # retain a no-thinking fallback if an intermediary rejects it.
        if is_official_deepseek_v4 and is_destiny_generation:
            non_thinking = {"thinking": {"type": "disabled"}}
            variants = [
                {**json_body, **non_thinking},
                {**base_body, **non_thinking},
                json_body,
                base_body,
            ]
        else:
            variants = [json_body, base_body]
        last_error: Exception | None = None
        for variant_index, body in enumerate(variants, start=1):
            variant = f"structured_variant_{variant_index}"
            attempt = self._begin_provider_attempt(
                request_kind,
                compatibility_variant=variant,
                retry_reason="compatibility_fallback" if variant_index > 1 else "",
            )
            try:
                response = self._client.post(endpoint, headers=headers, json=body, timeout=timeout)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._finish_provider_attempt(
                    attempt,
                    request_kind,
                    status="http_error",
                    compatibility_variant=variant,
                    retry_reason="compatibility_fallback" if variant_index > 1 else "",
                    http_status=exc.response.status_code,
                    error=str(exc),
                )
                if exc.response.status_code not in {400, 404, 422}:
                    raise
                last_error = exc
                continue
            except (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                self._finish_provider_attempt(
                    attempt,
                    request_kind,
                    status="transport_error",
                    compatibility_variant=variant,
                    retry_reason="compatibility_fallback" if variant_index > 1 else "",
                    error=str(exc),
                )
                raise
            try:
                payload = response.json()
                self._capture_usage(payload, config, request_kind)
                choices = payload.get("choices") or []
                choice = choices[0] if choices else {}
                finish_reason = str(choice.get("finish_reason") or "").strip().lower()
                content = _text_content((choice.get("message") or {}).get("content") if choice else None)
                if finish_reason in {"length", "max_tokens"}:
                    raise ValueError("模型输出达到长度上限，JSON 在完成前被截断")
            except Exception as exc:
                self._finish_provider_attempt(
                    attempt,
                    request_kind,
                    status="error",
                    compatibility_variant=variant,
                    retry_reason="compatibility_fallback" if variant_index > 1 else "",
                    http_status=response.status_code,
                    error=str(exc),
                )
                raise
            if content.strip():
                self._finish_provider_attempt(
                    attempt,
                    request_kind,
                    status="success",
                    compatibility_variant=variant,
                    retry_reason="compatibility_fallback" if variant_index > 1 else "",
                    http_status=response.status_code,
                )
                return content
            last_error = ValueError(f"{request_kind} response content is blank")
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="empty",
                compatibility_variant=variant,
                retry_reason="compatibility_fallback" if variant_index > 1 else "",
                http_status=response.status_code,
                error=str(last_error),
            )
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
        """Visible generation uses only standard Chat Completions fields."""

        variants = [True, False]
        last_error: Exception | None = None
        blank_retry_used = False
        for variant_index, include_usage in enumerate(variants):
            try:
                yield from self._stream_with_connect_retry(
                    messages,
                    config,
                    request_kind=request_kind,
                    include_usage=include_usage,
                    retry_reason="compatibility_fallback" if variant_index else "",
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
                    retry_reason="empty_output_retry",
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
        retry_reason: str = "",
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
                    retry_reason="connection_retry" if attempt else retry_reason,
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
        retry_reason: str = "",
    ) -> Iterator[str]:
        """发送真实 provider 请求并把 SSE delta.content 原样交给协议解析层。"""

        self._local.finish_reason = ""

        endpoint = f"{config.base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "stream": True,
        }
        self._apply_output_token_budget(body, config, config.max_tokens)
        if include_usage:
            body["stream_options"] = {"include_usage": True}
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        saw_content = False
        saw_reasoning = False
        finish_reason = ""
        started_at = monotonic()
        variant = f"stream_usage_{int(include_usage)}"
        attempt = self._begin_provider_attempt(
            request_kind,
            compatibility_variant=variant,
            retry_reason=retry_reason,
        )
        response_status: int | None = None
        try:
            with self._client.stream(
                "POST", endpoint, headers=headers, json=body, timeout=self.visible_timeout
            ) as response:
                response_status = response.status_code
                if response.is_error:
                    response.read()
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
                        self._capture_finish_reason(finish_reason)
            if not saw_content:
                detail = "reasoning-only" if saw_reasoning else "empty"
                if finish_reason:
                    detail += f", finish_reason={finish_reason}"
                raise EmptyVisibleContentError(f"{request_kind} response content is blank ({detail})")
        except httpx.HTTPStatusError as exc:
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="http_error",
                compatibility_variant=variant,
                retry_reason=retry_reason,
                http_status=exc.response.status_code,
                error=str(exc),
            )
            raise
        except EmptyVisibleContentError as exc:
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="empty",
                compatibility_variant=variant,
                retry_reason=retry_reason,
                http_status=response_status,
                error=str(exc),
            )
            raise
        except (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="transport_error",
                compatibility_variant=variant,
                retry_reason=retry_reason,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="error",
                compatibility_variant=variant,
                retry_reason=retry_reason,
                http_status=response_status,
                error=str(exc),
            )
            raise
        else:
            self._finish_provider_attempt(
                attempt,
                request_kind,
                status="success",
                compatibility_variant=variant,
                retry_reason=retry_reason,
                http_status=response_status,
            )
