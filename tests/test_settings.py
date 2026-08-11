from __future__ import annotations

from mindspace_graph.settings import AppSettings


def test_home_only_core_start_reopens_the_existing_home_data(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDSPACE_HOME", str(tmp_path / "Mindspace"))
    monkeypatch.delenv("MINDSPACE_RUNTIME_DIR", raising=False)

    settings = AppSettings.from_env()

    assert settings.runtime_dir == (tmp_path / "Mindspace").resolve()


def test_explicit_runtime_directory_still_has_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDSPACE_HOME", str(tmp_path / "Mindspace"))
    monkeypatch.setenv("MINDSPACE_RUNTIME_DIR", str(tmp_path / "chosen-data"))

    settings = AppSettings.from_env()

    assert settings.runtime_dir == (tmp_path / "chosen-data").resolve()


def test_explicit_data_root_is_canonical_and_does_not_nest_data(monkeypatch, tmp_path):
    install_root = tmp_path / "Mindspace"
    data_root = tmp_path / "MindspaceData"
    monkeypatch.setenv("MINDSPACE_HOME", str(install_root))
    monkeypatch.setenv("MINDSPACE_RUNTIME_DIR", str(install_root))
    monkeypatch.setenv("MINDSPACE_DATA_ROOT", str(data_root))

    settings = AppSettings.from_env()
    settings.ensure_directories()

    assert settings.runtime_dir == install_root.resolve()
    assert settings.data_root == data_root.resolve()
    assert (data_root / "sessions").is_dir()
    assert not (data_root / "data").exists()
