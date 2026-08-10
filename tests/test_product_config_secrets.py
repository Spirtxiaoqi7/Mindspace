from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import mindspace_graph.product_config as product_config_module
from mindspace_graph.api import create_app
from mindspace_graph.product_config import ProductConfigStore
from mindspace_graph.settings import AppSettings


def _settings(tmp_path, **overrides) -> AppSettings:
    values = {
        "runtime_dir": tmp_path / "runtime",
        "llm_mode": "openai",
        "tts_provider": "browser",
        "asr_provider": "browser",
        "role_audit_enabled": False,
    }
    values.update(overrides)
    return AppSettings(**values)


def _config_path(settings: AppSettings):
    return settings.runtime_dir / "config" / "settings.json"


def _disk_config(settings: AppSettings) -> dict:
    return json.loads(_config_path(settings).read_text(encoding="utf-8"))


def _assert_no_secret_fields(document: dict) -> None:
    assert "api_key" not in document["llm"]
    assert "asr_api_key" not in document["audio"]
    assert "tts_siliconflow_api_key" not in document["audio"]


def test_environment_secrets_survive_public_save_without_persistence(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        llm_api_key="launcher-llm-secret",
        asr_api_key="launcher-asr-secret",
        tts_siliconflow_api_key="launcher-tts-secret",
    )
    store = ProductConfigStore(_config_path(settings), settings)

    result = store.update({"llm": {"model": "public-model"}})

    assert settings.llm_api_key == "launcher-llm-secret"
    assert settings.asr_api_key == "launcher-asr-secret"
    assert settings.tts_siliconflow_api_key == "launcher-tts-secret"
    assert result["llm"]["credentials_configured"] is True
    assert result["llm"]["credentials_source"] == "runtime_environment"
    assert result["llm"]["credentials_persistence"] == "process_only"
    assert result["audio"]["asr_credentials_configured"] is True
    assert result["audio"]["tts_siliconflow_credentials_configured"] is True
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    assert disk["llm"]["model"] == "public-model"
    serialized = json.dumps(disk, ensure_ascii=False)
    assert "launcher-llm-secret" not in serialized
    assert "launcher-asr-secret" not in serialized
    assert "launcher-tts-secret" not in serialized


def test_legacy_plaintext_secrets_are_used_once_and_atomically_scrubbed(tmp_path) -> None:
    settings = _settings(tmp_path)
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "legacy-llm", "model": "legacy-model"},
                "audio": {
                    "asr_api_key": "legacy-asr",
                    "tts_siliconflow_api_key": "legacy-tts",
                },
            }
        ),
        encoding="utf-8",
    )

    store = ProductConfigStore(path, settings)

    assert settings.llm_api_key == "legacy-llm"
    assert settings.asr_api_key == "legacy-asr"
    assert settings.tts_siliconflow_api_key == "legacy-tts"
    snapshot = store.snapshot()
    assert snapshot["llm"]["credentials_source"] == "legacy_config"
    assert snapshot["audio"]["asr_credentials_source"] == "legacy_config"
    assert snapshot["audio"]["tts_siliconflow_credentials_source"] == "legacy_config"
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    serialized = json.dumps(disk)
    assert "legacy-llm" not in serialized
    assert "legacy-asr" not in serialized
    assert "legacy-tts" not in serialized


def test_launcher_secret_wins_over_legacy_plaintext_while_disk_is_scrubbed(tmp_path) -> None:
    settings = _settings(tmp_path, llm_api_key="launcher-wins")
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"llm": {"api_key": "stale-legacy", "model": "legacy-model"}}),
        encoding="utf-8",
    )

    store = ProductConfigStore(path, settings)

    assert settings.llm_api_key == "launcher-wins"
    assert store.snapshot()["llm"]["credentials_source"] == "runtime_environment"
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    assert "stale-legacy" not in json.dumps(disk)
    assert "launcher-wins" not in json.dumps(disk)


def test_runtime_secret_patch_is_process_only_and_public_fields_persist(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = ProductConfigStore(_config_path(settings), settings)

    result = store.update(
        {
            "llm": {"api_key": "patched-llm", "model": "patched-model"},
            "audio": {
                "asr_api_key": "patched-asr",
                "tts_siliconflow_api_key": "patched-tts",
            },
        }
    )

    assert settings.llm_api_key == "patched-llm"
    assert settings.asr_api_key == "patched-asr"
    assert settings.tts_siliconflow_api_key == "patched-tts"
    assert result["llm"]["credentials_source"] == "runtime_patch"
    assert result["llm"]["credentials_persisted"] is False
    assert result["llm"]["credentials_persistence"] == "process_only"
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    assert disk["llm"]["model"] == "patched-model"

    store.update({"llm": {"api_key": ""}})
    assert settings.llm_api_key == "patched-llm"

    cleared = store.update({"secret_operations": {"llm_api_key": "clear"}})
    assert settings.llm_api_key == ""
    assert cleared["llm"]["credentials_configured"] is False
    assert cleared["llm"]["credentials_source"] == "runtime_patch_clear"
    _assert_no_secret_fields(_disk_config(settings))


def test_atomic_public_save_failure_preserves_disk_and_runtime_state(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, llm_api_key="original-secret", llm_model="original-model")
    store = ProductConfigStore(_config_path(settings), settings)
    before = _config_path(settings).read_bytes()

    def fail_atomic_save(*_args, **_kwargs) -> None:
        raise OSError("injected atomic save failure")

    monkeypatch.setattr(product_config_module, "_atomic_json", fail_atomic_save)

    with pytest.raises(OSError, match="injected atomic save failure"):
        store.update({"llm": {"api_key": "replacement-secret", "model": "replacement-model"}})

    assert _config_path(settings).read_bytes() == before
    assert settings.llm_api_key == "original-secret"
    assert settings.llm_model == "original-model"
    assert store.checkpoint()["llm"]["model"] == "original-model"


def test_settings_get_and_patch_never_expose_or_persist_secrets(tmp_path) -> None:
    settings = _settings(tmp_path, llm_api_key="launcher-secret")
    app = create_app(settings)
    client = TestClient(app)

    public = client.get("/api/v1/settings")
    assert public.status_code == 200
    public_text = public.text
    assert "launcher-secret" not in public_text
    assert '"api_key"' not in public_text
    assert public.json()["llm"]["credentials_configured"] is True

    changed = client.patch(
        "/api/v1/settings",
        json={"llm": {"api_key": "browser-process-secret", "model": "browser-model"}},
    )
    assert changed.status_code == 200
    assert "browser-process-secret" not in changed.text
    assert settings.llm_api_key == "browser-process-secret"
    assert changed.json()["settings"]["llm"]["credentials_source"] == "runtime_patch"
    assert changed.json()["settings"]["llm"]["credentials_persistence"] == "process_only"
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    assert disk["llm"]["model"] == "browser-model"
    assert "browser-process-secret" not in json.dumps(disk)

    cleared = client.patch(
        "/api/v1/settings",
        json={"secret_operations": {"llm_api_key": "clear"}},
    )
    assert cleared.status_code == 200
    assert settings.llm_api_key == ""
    assert cleared.json()["settings"]["llm"]["credentials_configured"] is False
    assert '"api_key"' not in cleared.text


def test_launcher_environment_secret_reaches_model_adapter_without_disk_copy(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "launcher-runtime"
    monkeypatch.setenv("MINDSPACE_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("MINDSPACE_LLM_MODE", "openai")
    monkeypatch.setenv("MINDSPACE_LLM_API_KEY", "launcher-adapter-secret")
    monkeypatch.setenv("MINDSPACE_ASR_API_KEY", "launcher-asr-adapter-secret")
    monkeypatch.setenv("MINDSPACE_TTS_SILICONFLOW_API_KEY", "launcher-tts-adapter-secret")
    monkeypatch.setenv("MINDSPACE_TTS_PROVIDER", "browser")
    monkeypatch.setenv("MINDSPACE_ASR_PROVIDER", "browser")
    monkeypatch.setenv("MINDSPACE_ROLE_AUDIT_ENABLED", "false")

    settings = AppSettings.from_env()
    app = create_app(settings)
    adapter_config = app.state.container.conversation._role_audit_api()

    assert adapter_config.api_key == "launcher-adapter-secret"
    assert settings.asr_api_key == "launcher-asr-adapter-secret"
    assert settings.tts_siliconflow_api_key == "launcher-tts-adapter-secret"
    disk = _disk_config(settings)
    _assert_no_secret_fields(disk)
    assert "launcher-adapter-secret" not in json.dumps(disk)
    assert "launcher-asr-adapter-secret" not in json.dumps(disk)
    assert "launcher-tts-adapter-secret" not in json.dumps(disk)
