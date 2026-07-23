from __future__ import annotations

from mindspace_graph.emotion_disabled import DisabledEmotionCoordinator
from mindspace_graph.settings import AppSettings


def test_emotion_is_forced_off_even_when_legacy_environment_requests_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDSPACE_HOME", str(tmp_path))
    monkeypatch.setenv("MINDSPACE_EMOTION_ENABLED", "1")

    settings = AppSettings.from_env()

    assert settings.emotion_enabled is False
    assert settings.public_config()["emotion_enabled"] is False


def test_disabled_emotion_adapter_has_no_work_or_state():
    adapter = DisabledEmotionCoordinator()

    assert adapter.enabled() is False
    assert adapter.previous_for_round("session", 2) is None
    assert adapter.schedule_post_turn("session", 1, lambda: None) is None
    assert adapter.close() is None
