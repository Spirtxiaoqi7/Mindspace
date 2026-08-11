from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.models import ApiConfig


def test_official_deepseek_v4_disables_default_thinking() -> None:
    body: dict[str, object] = {}

    OpenAICompatibleLanguageModel._apply_provider_request_compat(
        body,
        ApiConfig(base_url="https://api.deepseek.com", model="deepseek-v4-flash"),
    )

    assert body == {"thinking": {"type": "disabled"}}


def test_provider_compat_does_not_leak_to_other_openai_endpoints() -> None:
    body: dict[str, object] = {}

    OpenAICompatibleLanguageModel._apply_provider_request_compat(
        body,
        ApiConfig(base_url="https://api.openai.com/v1", model="gpt-5"),
    )

    assert body == {}


def test_output_budget_uses_openai_completion_field_for_current_models() -> None:
    body: dict[str, object] = {}
    config = ApiConfig(base_url="https://api.openai.com/v1", model="gpt-5")

    OpenAICompatibleLanguageModel._apply_output_token_budget(body, config, 4096)

    assert body == {"max_completion_tokens": 4096}


def test_output_budget_keeps_compatible_max_tokens_for_other_providers() -> None:
    body: dict[str, object] = {}
    config = ApiConfig(base_url="https://api.siliconflow.com/v1", model="example/model")

    OpenAICompatibleLanguageModel._apply_output_token_budget(body, config, 8192)

    assert body == {"max_tokens": 8192}
