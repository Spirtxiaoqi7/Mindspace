from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.models import ApiConfig


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
