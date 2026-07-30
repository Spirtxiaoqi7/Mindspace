from __future__ import annotations

from mindspace_graph.settings import AppSettings


def test_home_only_core_start_reopens_the_existing_home_data(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDSPACE_HOME", str(tmp_path / "Mindspace"))
    monkeypatch.delenv("MINDSPACE_RUNTIME_DIR", raising=False)

    settings = AppSettings.from_env()

    assert settings.runtime_dir == (tmp_path / "Mindspace" / "data").resolve()


def test_explicit_runtime_directory_still_has_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDSPACE_HOME", str(tmp_path / "Mindspace"))
    monkeypatch.setenv("MINDSPACE_RUNTIME_DIR", str(tmp_path / "chosen-data"))

    settings = AppSettings.from_env()

    assert settings.runtime_dir == (tmp_path / "chosen-data").resolve()
