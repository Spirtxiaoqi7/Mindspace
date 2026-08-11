from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from mindspace_graph.api import create_app
from mindspace_graph.models import ChatRequest
from mindspace_graph.settings import AppSettings


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
        role_audit_enabled=False,
    )


def _profile_content(document: dict) -> dict:
    content = deepcopy(document)
    content.pop("revision", None)
    content.pop("updated_at", None)
    return content


def test_settings_refresh_failure_restores_config_profile_and_clears_journal(tmp_path, monkeypatch) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    container = app.state.container
    previous_config = container.config.checkpoint()
    previous_profile = deepcopy(container.profiles.load_document("user_profile"))
    original_refresh = container.conversation.refresh_language_model
    refresh_attempts = 0

    def fail_once_then_refresh() -> None:
        nonlocal refresh_attempts
        refresh_attempts += 1
        if refresh_attempts == 1:
            raise RuntimeError("injected primary LLM refresh failure")
        original_refresh()

    monkeypatch.setattr(container.conversation, "refresh_language_model", fail_once_then_refresh)

    response = client.put("/api/v1/settings", json={"persona": {"user_name": "回滚验收"}})

    assert response.status_code >= 400
    assert container.config.checkpoint() == previous_config
    restored_profile = container.profiles.load_document("user_profile")
    assert _profile_content(restored_profile) == _profile_content(previous_profile)
    assert container.database.get_document("settings-transaction:pending") is None
    assert refresh_attempts == 2


def test_startup_recovers_legacy_settings_journal_without_transaction_id(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    container = app.state.container
    previous_config = container.config.checkpoint()
    previous_profile = deepcopy(container.profiles.load_document("user_profile"))
    container.config.update({"persona": {"user_name": "中断后的名称"}})
    changed_profile = container.profiles.load_document("user_profile")
    changed_profile.setdefault("identity", {})["preferred_name"] = "中断后的名称"
    container.profiles.save_document("user_profile", changed_profile)
    container.database.put_document(
        "settings-transaction:pending",
        {
            "schema_version": "1.0.0",
            "revision": 1,
            "state": "prepared",
            "previous_config": previous_config,
            "previous_profile": previous_profile,
        },
    )

    recovered = create_app(_settings(tmp_path)).state.container

    assert recovered.config.checkpoint() == previous_config
    restored_profile = recovered.profiles.load_document("user_profile")
    assert _profile_content(restored_profile) == _profile_content(previous_profile)
    assert recovered.database.get_document("settings-transaction:pending") is None


def test_settings_rollback_preserves_a_concurrent_profile_edit(tmp_path, monkeypatch) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    container = app.state.container
    original_refresh = container.conversation.refresh_language_model
    refresh_attempts = 0

    def write_concurrent_profile_then_fail() -> None:
        nonlocal refresh_attempts
        refresh_attempts += 1
        if refresh_attempts == 1:
            profile = container.profiles.load_document("user_profile")
            profile["custom_profile"] = "并发保存的补充资料"
            container.profiles.save_document("user_profile", profile)
            raise RuntimeError("injected refresh failure after concurrent profile save")
        original_refresh()

    monkeypatch.setattr(
        container.conversation,
        "refresh_language_model",
        write_concurrent_profile_then_fail,
    )

    response = client.put("/api/v1/settings", json={"persona": {"user_name": "事务名称"}})

    restored = container.profiles.load_document("user_profile")
    assert response.status_code >= 400
    assert restored["identity"]["preferred_name"] == "用户"
    assert restored["custom_profile"] == "并发保存的补充资料"
    assert container.database.get_document("settings-transaction:pending") is None


def test_request_digest_rejects_changed_payload_after_restart(tmp_path) -> None:
    original = ChatRequest(message="第一条消息", session_id="phase1-idempotency", round=1)
    changed = ChatRequest(message="第二条消息", session_id="phase1-idempotency", round=1)
    assert original.idempotency_digest() == original.idempotency_digest()
    assert original.idempotency_digest() != changed.idempotency_digest()

    first = TestClient(create_app(_settings(tmp_path))).post(
        "/api/v1/chat/stream",
        json=original.model_dump(mode="json"),
        headers={"X-Request-ID": "phase1-idempotency-run"},
    )
    restarted = TestClient(create_app(_settings(tmp_path))).post(
        "/api/v1/chat/stream",
        json=changed.model_dump(mode="json"),
        headers={"X-Request-ID": "phase1-idempotency-run"},
    )

    assert first.status_code == 200
    assert restarted.status_code == 409
