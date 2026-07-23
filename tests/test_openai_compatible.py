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
