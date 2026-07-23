from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "deploy-installer.py"
SPEC = importlib.util.spec_from_file_location("deploy_installer", SCRIPT)
assert SPEC and SPEC.loader
deploy_installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_installer)


def write_test_pe(path: Path, *, signed: bool) -> None:
    data = bytearray(1024 * 1024)
    pe_offset = 0x80
    optional_offset = pe_offset + 24
    data[0:2] = b"MZ"
    data[0x3C:0x40] = pe_offset.to_bytes(4, "little")
    data[pe_offset:pe_offset + 4] = b"PE\0\0"
    data[optional_offset:optional_offset + 2] = (0x20B).to_bytes(2, "little")
    if signed:
        certificate_entry = optional_offset + 112 + (8 * 4)
        data[certificate_entry:certificate_entry + 4] = (0xF0000).to_bytes(4, "little")
        data[certificate_entry + 4:certificate_entry + 8] = (128).to_bytes(4, "little")
    path.write_bytes(data)


def test_manifest_contains_required_stable_fields_and_matching_hash(tmp_path: Path) -> None:
    installer = tmp_path / "Mindspace-0.6.0-x64.exe"
    write_test_pe(installer, signed=False)
    manifest, digest = deploy_installer.build_download_manifest(
        installer,
        installer.name,
        "0.6.0",
        "douyinqijun.cn",
        published_at="2026-07-22T12:00:00Z",
    )
    assert manifest["product"] == "Mindspace"
    assert manifest["channel"] == "stable"
    assert manifest["signature_status"] == "unsigned"
    assert manifest["published_at"] == "2026-07-22T12:00:00Z"
    assert manifest["bytes"] == installer.stat().st_size
    assert manifest["sha256"] == digest == hashlib.sha256(installer.read_bytes()).hexdigest()
    assert manifest["url"].endswith(installer.name)


def test_signature_status_is_detected_from_pe_certificate_table(tmp_path: Path) -> None:
    installer = tmp_path / "Mindspace-0.6.1-x64.exe"
    write_test_pe(installer, signed=True)
    manifest, _ = deploy_installer.build_download_manifest(
        installer, installer.name, "0.6.1", "douyinqijun.cn"
    )
    assert manifest["signature_status"] == "signed"


def test_declared_signed_rejects_unsigned_file(tmp_path: Path) -> None:
    installer = tmp_path / "Mindspace-0.6.2-x64.exe"
    write_test_pe(installer, signed=False)
    with pytest.raises(ValueError, match="no PE certificate table"):
        deploy_installer.build_download_manifest(
            installer,
            installer.name,
            "0.6.2",
            "douyinqijun.cn",
            signature_status="signed",
        )


@pytest.mark.parametrize(
    ("name", "version", "domain"),
    [
        ("Mindspace-0.7.0-x64.zip", "0.7.0", "douyinqijun.cn"),
        ("Mindspace-0.7.1-x64.exe", "0.7.0", "douyinqijun.cn"),
        ("Mindspace-0.7.0-x64.exe", "0.7.0", "example.com"),
    ],
)
def test_manifest_rejects_inconsistent_public_identity(
    tmp_path: Path, name: str, version: str, domain: str
) -> None:
    installer = tmp_path / name
    write_test_pe(installer, signed=False)
    with pytest.raises(ValueError):
        deploy_installer.build_download_manifest(installer, name, version, domain)


def test_release_script_never_rewrites_website_html() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def deploy_download_page" not in source
    assert "PAGE_LINK_REPLACEMENTS" not in source
    assert "index.html.partial" not in source
