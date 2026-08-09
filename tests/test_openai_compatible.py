import json

import httpx

from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.models import ApiConfig


def test_private_structured_calls_disable_thinking_and_request_json():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"trigger":"none","patches":[]}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    result = model.extract_memory(
        [{"role": "user", "content": "{}"}],
        ApiConfig(api_key="test"),
        timeout_seconds=2,
    )

    assert json.loads(result)["trigger"] == "none"
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert model.take_usage().request_kind == "memory_extract"
    client.close()


def test_character_structured_generation_records_compatible_usage_kind():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"summary":"温柔"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    result = model.generate_structured(
        [{"role": "user", "content": "json"}],
        ApiConfig(api_key="test"),
        request_kind="character_generate",
    )

    assert result == '{"summary":"温柔"}'
    assert model.take_usage().request_kind == "character_generate"
    client.close()


def test_compaction_disables_thinking_and_requests_json():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"summary":"完成"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    result = model.compact(
        [{"role": "user", "content": "压缩"}],
        ApiConfig(api_key="test", max_tokens=1200),
    )

    assert json.loads(result)["summary"] == "完成"
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert model.take_usage().request_kind == "compaction"
    client.close()


def test_role_audit_uses_structured_compatibility_ladder():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": "unknown field"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.audit_role([], ApiConfig(api_key="test", max_tokens=1000)) == "{}"
    assert len(bodies) == 3
    assert "thinking" not in bodies[-1]
    assert "response_format" not in bodies[-1]
    client.close()


def test_private_structured_calls_fall_back_for_generic_compatible_servers():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": "unknown field"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.plan_capabilities([], ApiConfig()) == "{}"
    assert len(bodies) == 3
    assert "thinking" not in bodies[-1]
    client.close()


def test_stream_retries_connect_handshake_before_first_token():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError(
                "[SSL: WRONG_VERSION_NUMBER] wrong version number",
                request=request,
            )
        return httpx.Response(
            200,
            text=('data: {"choices":[{"delta":{"content":"恢复"}}]}\n\ndata: [DONE]\n\n'),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.generate([], ApiConfig(api_key="test")) == "恢复"
    assert attempts == 2
    client.close()


def test_visible_stream_disables_thinking_by_default():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"正文"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.generate([], ApiConfig(api_key="test")) == "正文"
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[0]["stream_options"] == {"include_usage": True}
    client.close()


def test_visible_stream_falls_back_when_compatibility_fields_are_rejected():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "stream_options" in body or "thinking" in body:
            return httpx.Response(400, json={"error": "unknown field"})
        return httpx.Response(
            200,
            text=('data: {"choices":[{"delta":{"content":"兼容"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.generate([], ApiConfig(api_key="test")) == "兼容"
    assert len(bodies) == 3
    assert "thinking" not in bodies[-1]
    assert "stream_options" not in bodies[-1]
    client.close()


def test_visible_stream_retries_one_reasoning_only_result_before_ui_output():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"reasoning_content":"内部推理"},'
                    '"finish_reason":null}]}\n\n'
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            text=('data: {"choices":[{"delta":{"content":"重试正文"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)

    assert model.generate([], ApiConfig(api_key="test")) == "重试正文"
    assert len(bodies) == 2
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert bodies[1]["thinking"] == {"type": "disabled"}
    assert "stream_options" not in bodies[1]
    client.close()


def test_visible_stream_accepts_block_array_content():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":'
                '[{"type":"text","text":"数组正文"}]},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleLanguageModel(client=client)
    assert model.generate([], ApiConfig(api_key="test")) == "数组正文"
    client.close()
