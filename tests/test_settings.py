from __future__ import annotations

from mindspace_graph.settings import AppSettings


def test_dotenv_loads_server_secrets_but_public_config_redacts_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINDSPACE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MINDSPACE_LLM_MODE", raising=False)
    (tmp_path / ".env").write_text(
        "MINDSPACE_LLM_MODE=openai\nMINDSPACE_LLM_API_KEY=secret-value\n",
        encoding="utf-8",
    )

    settings = AppSettings.from_env()
    public = settings.public_config()

    assert settings.llm_mode == "openai"
    assert settings.llm_api_key == "secret-value"
    assert "secret-value" not in str(public)
    assert "api_key" not in public
