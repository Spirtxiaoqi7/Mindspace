from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from mindspace_graph.art_catalog import ArtCatalogService
from mindspace_graph.static_paths import BUILTIN_ART_ARCHIVE_ROOT, BUILTIN_ART_MANIFEST, STATIC_APP_ROOT


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builtin_art_manifest_v2_and_approved_expansion_are_valid():
    web_root = STATIC_APP_ROOT
    archive_root = BUILTIN_ART_ARCHIVE_ROOT
    manifest = json.loads(BUILTIN_ART_MANIFEST.read_text(encoding="utf-8"))
    preview_index = json.loads((archive_root / "previews" / "index.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "2.0.0"
    assert manifest["preview_index"] == "/assets/archive/previews/index.json"
    assert manifest["approval_status"] == "approved_for_expansion"
    assert preview_index["approval_status"] == "approved_2026-07-30"
    assert len(preview_index["items"]) == 12
    assert len(manifest["assets"]) == 171
    assert manifest["categories"] == {
        "svg_icons": 72,
        "scrapbook_stickers": 24,
        "frames": 16,
        "textures": 12,
        "state_illustrations": 12,
        "scene_thumbnails": 20,
        "journal_covers": 8,
        "compatibility_extras": 7,
    }
    assert sum(path.stat().st_size for path in archive_root.rglob("*") if path.is_file()) < (15 * 1024 * 1024)

    for item in preview_index["items"]:
        path = web_root / item["path"].removeprefix("/assets/")
        assert path.is_file()

    for asset in manifest["assets"]:
        path = web_root / asset["path"].removeprefix("/assets/").split("#", 1)[0]
        assert path.is_file()
        assert path.stat().st_size == asset["bytes"]
        assert sha256(path) == asset["sha256"]
        assert asset["pack_id"] == "core"
        assert asset["theme"] == "anime-scrapbook"
        assert asset["usage"]
        assert set(asset["dimensions"]) == {"width", "height"}
        assert asset["min_app_version"] in {"0.6.0", "0.7.0"}

    icon_path = archive_root / "icons" / "icon-journal.svg"
    icon = icon_path.read_text(encoding="utf-8")
    assert 'width="64"' in icon
    assert 'height="64"' in icon
    assert 'viewBox="4 4 56 56"' in icon


def test_packaged_static_root_contains_shell_manifest_scene_assets_avatars_and_worklets():
    manifest = json.loads(BUILTIN_ART_MANIFEST.read_text(encoding="utf-8"))
    assert (STATIC_APP_ROOT / "index.html").is_file()
    assert (STATIC_APP_ROOT / "avatar-ai-default.webp").is_file()
    assert (STATIC_APP_ROOT / "avatar-user-default.webp").is_file()
    assert (STATIC_APP_ROOT / "pcm-worklet.js").is_file()
    assert (STATIC_APP_ROOT / "tts-playback-worklet.js").is_file()
    scene_assets = [item for item in manifest["assets"] if "/scenes/" in item["path"]]
    assert scene_assets
    assert all((STATIC_APP_ROOT / item["path"].removeprefix("/assets/")).is_file() for item in scene_assets)


def test_pack_extraction_rolls_back_corrupt_replacement(tmp_path):
    manifest_path = tmp_path / "builtin.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "2.0.0", "assets": [], "packs": []}),
        encoding="utf-8",
    )
    service = ArtCatalogService(manifest_path, tmp_path / "packs")
    target = tmp_path / "packs" / "season-night"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text('{"old":true}', encoding="utf-8")
    old_bytes = (target / "manifest.json").read_bytes()

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "pack_id": "season-night",
                    "assets": [
                        {
                            "relative_path": "scene.webp",
                            "sha256": "0" * 64,
                        }
                    ],
                }
            ),
        )
        bundle.writestr("scene.webp", b"not-the-declared-content")

    with pytest.raises(ValueError, match="integrity"):
        service._extract("season-night", archive, target)
    assert (target / "manifest.json").read_bytes() == old_bytes
    assert not (tmp_path / "packs" / ".season-night.staging").exists()
    service.close()


def test_pack_rejects_zip_path_traversal(tmp_path):
    manifest_path = tmp_path / "builtin.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "2.0.0", "assets": [], "packs": []}),
        encoding="utf-8",
    )
    service = ArtCatalogService(manifest_path, tmp_path / "packs")
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", b"no")

    with pytest.raises(ValueError, match="unsafe"):
        service._extract("unsafe", archive, tmp_path / "packs" / "unsafe")
    assert not (tmp_path / "outside.txt").exists()
    service.close()


def test_pack_pause_state_preserves_partial_bytes_for_resume(tmp_path):
    manifest_path = tmp_path / "builtin.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0.0",
                "assets": [],
                "packs": [
                    {
                        "pack_id": "season-night",
                        "bytes": 100,
                        "sha256": "0" * 64,
                        "download_urls": ["https://assets.example.com/season.zip"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ArtCatalogService(manifest_path, tmp_path / "packs")
    partial = tmp_path / "packs" / ".downloads" / "season-night.zip.part"
    partial.write_bytes(b"partial")

    paused = service.pause("season-night")
    assert paused == {
        "pack_id": "season-night",
        "status": "paused",
        "downloaded_bytes": 7,
    }
    status = service.packs()[0]
    assert status["status"] == "paused"
    assert status["downloaded_bytes"] == 7
    assert partial.read_bytes() == b"partial"
    service.close()
