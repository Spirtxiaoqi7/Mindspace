from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "extract_voice_archive",
    ROOT / "scripts" / "extract-voice-archive.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extract_tar_rejects_path_escape(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        payload = b"unsafe"
        info = tarfile.TarInfo("../outside.txt")
        info.size = len(payload)
        output.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="escapes target"):
        MODULE.extract_tar(archive, tmp_path / "output")


def test_extract_zip_writes_regular_files(tmp_path: Path) -> None:
    archive = tmp_path / "voice.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("v4/角色/reference.wav", b"RIFF")
    destination = tmp_path / "output"
    destination.mkdir()
    MODULE.extract_zip(archive, destination, "gbk")
    assert (destination / "v4" / "角色" / "reference.wav").read_bytes() == b"RIFF"
