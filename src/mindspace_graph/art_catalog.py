"""Versioned art catalog and resumable optional-pack installer."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shutil
import socket
import threading
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class ArtPackPaused(RuntimeError):
    """Raised after preserving a resumable partial download."""


class ArtCatalogService:
    """Resolve bundled assets first and install only catalog-declared packs."""

    def __init__(self, builtin_manifest: Path, pack_root: Path) -> None:
        self.builtin_manifest = builtin_manifest
        self.pack_root = pack_root
        self.pack_root.mkdir(parents=True, exist_ok=True)
        self.download_root = pack_root / ".downloads"
        self.download_root.mkdir(parents=True, exist_ok=True)
        self._install_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._paused: set[str] = set()
        self._http = httpx.Client(
            timeout=httpx.Timeout(60, connect=10),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    def close(self) -> None:
        self._http.close()

    def catalog(self) -> dict[str, Any]:
        builtin = self._load_json(self.builtin_manifest)
        assets = list(builtin.get("assets") or [])
        installed: list[dict[str, Any]] = []
        for manifest_path in sorted(self.pack_root.glob("*/manifest.json")):
            try:
                pack = self._load_json(manifest_path)
            except (OSError, ValueError):
                continue
            installed.append(
                {
                    "pack_id": pack.get("pack_id"),
                    "version": pack.get("version"),
                    "status": "installed",
                    "asset_count": len(pack.get("assets") or []),
                }
            )
            assets.extend(pack.get("assets") or [])
        return {
            **builtin,
            "assets": assets,
            "installed_packs": installed,
            "asset_count": len(assets),
        }

    def packs(self) -> list[dict[str, Any]]:
        manifest = self._load_json(self.builtin_manifest)
        installed = {item["pack_id"]: item for item in self.catalog().get("installed_packs") or []}
        result = []
        for pack in manifest.get("packs") or []:
            item = dict(pack)
            item["status"] = "installed" if item.get("pack_id") in installed else "available"
            part = self.download_root / f"{item.get('pack_id')}.zip.part"
            item["downloaded_bytes"] = part.stat().st_size if part.exists() else 0
            with self._state_lock:
                if item["status"] != "installed" and item.get("pack_id") in self._paused:
                    item["status"] = "paused"
            result.append(item)
        return result

    def install(self, pack_id: str) -> dict[str, Any]:
        with self._state_lock:
            self._paused.discard(pack_id)
        with self._install_lock:
            return self._install_locked(pack_id)

    def pause(self, pack_id: str) -> dict[str, Any]:
        pack = next(
            (item for item in self.packs() if item.get("pack_id") == pack_id),
            None,
        )
        if pack is None:
            raise KeyError("art pack not found")
        if pack.get("status") == "installed":
            return {
                "pack_id": pack_id,
                "status": "installed",
                "downloaded_bytes": 0,
            }
        with self._state_lock:
            self._paused.add(pack_id)
        part = self.download_root / f"{pack_id}.zip.part"
        return {
            "pack_id": pack_id,
            "status": "paused",
            "downloaded_bytes": part.stat().st_size if part.exists() else 0,
        }

    def _install_locked(self, pack_id: str) -> dict[str, Any]:
        pack = next((item for item in self.packs() if item.get("pack_id") == pack_id), None)
        if pack is None:
            raise KeyError("art pack not found")
        target = self.pack_root / pack_id
        if target.is_dir() and (target / "manifest.json").is_file():
            return {"pack_id": pack_id, "status": "installed", "reused": True}
        urls = [str(item) for item in pack.get("download_urls") or [] if item]
        if not urls:
            raise ValueError("art pack has no configured download source")
        expected_sha = str(pack.get("sha256") or "").lower()
        expected_bytes = int(pack.get("bytes") or 0)
        if len(expected_sha) != 64 or expected_bytes <= 0:
            raise ValueError("art pack integrity metadata is incomplete")
        archive = self.download_root / f"{pack_id}.zip.part"
        error = ""
        for url in urls:
            try:
                self._validate_public_https(url)
                self._download(pack_id, url, archive, expected_bytes)
                if archive.stat().st_size != expected_bytes:
                    raise ValueError("art pack size mismatch")
                if self._sha256(archive) != expected_sha:
                    raise ValueError("art pack checksum mismatch")
                self._extract(pack_id, archive, target)
                archive.unlink(missing_ok=True)
                return {"pack_id": pack_id, "status": "installed", "reused": False}
            except ArtPackPaused:
                raise
            except Exception as exc:  # noqa: BLE001 - try the next trusted mirror
                error = str(exc)
                # A complete file with a bad digest cannot be resumed safely from
                # another mirror. Partial files remain available for a real Range
                # resume, while corrupt complete files are discarded.
                if archive.exists() and archive.stat().st_size >= expected_bytes:
                    archive.unlink(missing_ok=True)
        raise RuntimeError(f"art pack download failed: {error}")

    def _download(self, pack_id: str, url: str, target: Path, expected_bytes: int) -> None:
        existing = target.stat().st_size if target.exists() else 0
        if existing > expected_bytes:
            target.unlink()
            existing = 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        with self._http.stream("GET", url, headers=headers) as response:
            if response.status_code not in ({206} if existing else {200, 206}):
                raise RuntimeError(f"download returned HTTP {response.status_code}")
            if existing and response.status_code == 200:
                target.unlink(missing_ok=True)
                existing = 0
            mode = "ab" if existing else "wb"
            with target.open(mode) as handle:
                for chunk in response.iter_bytes(256 * 1024):
                    with self._state_lock:
                        paused = pack_id in self._paused
                    if paused:
                        raise ArtPackPaused("art pack download paused")
                    handle.write(chunk)

    def _extract(self, pack_id: str, archive: Path, target: Path) -> None:
        staging = self.pack_root / f".{pack_id}.staging"
        backup = self.pack_root / f".{pack_id}.rollback"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                infos = bundle.infolist()
                if not infos or len(infos) > 500:
                    raise ValueError("art pack file count is invalid")
                total = 0
                for info in infos:
                    path = Path(info.filename)
                    total += info.file_size
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or len(info.filename) > 240
                        or info.file_size > 32 * 1024 * 1024
                        or total > 256 * 1024 * 1024
                    ):
                        raise ValueError("unsafe art pack entry")
                bundle.extractall(staging)
            manifest = self._load_json(staging / "manifest.json")
            if manifest.get("pack_id") != pack_id:
                raise ValueError("art pack manifest id mismatch")
            for asset in manifest.get("assets") or []:
                relative = Path(str(asset.get("relative_path") or ""))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe asset path")
                path = staging / relative
                if not path.is_file() or self._sha256(path) != asset.get("sha256"):
                    raise ValueError("art pack asset integrity check failed")
            if target.exists():
                target.replace(backup)
            try:
                staging.replace(target)
            except Exception:
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("art manifest must be an object")
        return value

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_public_https(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("art packs require an HTTPS source")
        for record in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(record[4][0])
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
            ):
                raise ValueError("art pack source resolved to a non-public address")
