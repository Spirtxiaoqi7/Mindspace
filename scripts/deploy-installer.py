"""Inspect a release host and atomically upload a signed Mindspace installer."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path

import paramiko


def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return f"SHA256:{base64.b64encode(digest).decode().rstrip('=')}"


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    password = os.environ.get("MINDSPACE_DEPLOY_PASSWORD", "")
    if not password:
        raise RuntimeError("MINDSPACE_DEPLOY_PASSWORD is required")
    last_error: Exception | None = None
    for attempt in range(1, 5):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                args.host,
                username=args.user,
                password=password,
                look_for_keys=False,
                allow_agent=False,
                timeout=30,
                banner_timeout=45,
                auth_timeout=30,
            )
            transport = client.get_transport()
            if transport is None:
                raise RuntimeError("SSH transport is unavailable after authentication")
            actual = fingerprint(transport.get_remote_server_key())
            if actual != args.fingerprint:
                raise RuntimeError(f"SSH host key mismatch: {actual}")
            if attempt > 1:
                print(f"SSH_CONNECTED_AFTER_RETRY={attempt}")
            return client
        except (OSError, paramiko.SSHException, RuntimeError) as error:
            client.close()
            last_error = error
            if "host key mismatch" in str(error).lower() or attempt == 4:
                raise
            print(f"SSH_RETRY={attempt}", flush=True)
            time.sleep(attempt * 4)
    raise RuntimeError(f"SSH connection failed: {last_error}")


def run(client: paramiko.SSHClient, command: str) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=30)
    status = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    if status:
        raise RuntimeError((error or output or f"remote command failed: {status}").strip())
    return output


def inspect(client: paramiko.SSHClient, domain: str) -> None:
    command = r"""
set -eu
printf '%s\n' '--- system ---'
uname -a
printf '%s\n' '--- disk ---'
df -h / /var/www /www 2>/dev/null || df -h /
printf '%s\n' '--- services ---'
command -v nginx || true
systemctl is-active nginx 2>/dev/null || true
systemctl is-active httpd 2>/dev/null || true
printf '%s\n' '--- domain config ---'
if command -v nginx >/dev/null 2>&1; then
  nginx -T 2>/dev/null \
    | awk '/server_name|^[[:space:]]*root |^[[:space:]]*listen / {print}' \
    | grep -B4 -A8 DOMAIN || true
fi
printf '%s\n' '--- candidate roots ---'
find /var/www /www/wwwroot -maxdepth 3 -type f \
  \( -name 'index.html' -o -name 'index.htm' \) \
  -printf '%p %s bytes\n' 2>/dev/null | head -40
printf '%s\n' '--- ports ---'
ss -lnt 2>/dev/null | grep -E ':(80|443)[[:space:]]' || true
""".replace("DOMAIN", shlex.quote(domain))
    print(run(client, command), end="")


def upload(client: paramiko.SSHClient, local: Path, remote_dir: str, remote_name: str) -> None:
    if not local.is_file():
        raise FileNotFoundError(local)
    run(client, f"install -d -m 0755 {shlex.quote(remote_dir)}")
    remote = posixpath.join(remote_dir, remote_name)
    partial = f"{remote}.partial"
    digest = hashlib.sha256()
    with local.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    local_hash = digest.hexdigest()
    expected_size = local.stat().st_size
    existing = run(
        client,
        f"if test -f {shlex.quote(remote)}; "
        f"then stat -c '%s' {shlex.quote(remote)}; else echo 0; fi",
    ).strip()
    if existing == str(expected_size):
        remote_hash = run(client, f"sha256sum {shlex.quote(remote)} | awk '{{print $1}}'").strip()
        if remote_hash == local_hash:
            print(f"REMOTE_REUSED={remote}")
            print(f"SHA256={local_hash}")
            print(f"BYTES={expected_size}")
            return
    last_percent = -5

    def progress(sent: int, total: int) -> None:
        nonlocal last_percent
        percent = int(sent * 100 / max(1, total))
        if percent >= last_percent + 5 or sent == total:
            last_percent = percent
            print(f"UPLOAD_PROGRESS={percent}", flush=True)

    partial_size = int(
        run(
            client,
            f"if test -f {shlex.quote(partial)}; "
            f"then stat -c '%s' {shlex.quote(partial)}; else echo 0; fi",
        ).strip()
    )
    if partial_size > expected_size:
        run(client, f"rm -f {shlex.quote(partial)}")
        partial_size = 0
    if partial_size < expected_size:
        prepare = "" if partial_size else f": > {shlex.quote(partial)} && "
        stdin, stdout, stderr = client.exec_command(
            f"{prepare}cat >> {shlex.quote(partial)}", bufsize=0
        )
        with local.open("rb") as source:
            source.seek(partial_size)
            sent = partial_size
            progress(sent, expected_size)
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                stdin.channel.sendall(chunk)
                sent += len(chunk)
                progress(sent, expected_size)
        stdin.channel.shutdown_write()
        status = stdout.channel.recv_exit_status()
        error = stderr.read().decode("utf-8", errors="replace")
        if status:
            raise RuntimeError(error.strip() or f"remote append failed: {status}")
    remote_size = int(run(client, f"stat -c '%s' {shlex.quote(partial)}").strip())
    if remote_size != expected_size:
        raise RuntimeError(
            f"remote partial size mismatch after upload: {remote_size}/{expected_size}"
        )
    remote_hash = run(client, f"sha256sum {shlex.quote(partial)} | awk '{{print $1}}'").strip()
    if remote_hash != local_hash:
        run(client, f"rm -f {shlex.quote(partial)}")
        raise RuntimeError("remote installer SHA-256 mismatch")
    run(
        client,
        f"chmod 0644 {shlex.quote(partial)} && mv -f {shlex.quote(partial)} {shlex.quote(remote)}",
    )
    print(f"REMOTE_PATH={remote}")
    print(f"SHA256={local_hash}")
    print(f"BYTES={expected_size}")


def promote_staged_file(client: paramiko.SSHClient, staged_path: str, published_path: str) -> bool:
    """Copy an already uploaded staging artifact into its public location.

    The regular upload path still runs afterwards and verifies both size and
    SHA-256 against the local release artifact before the public pointers move.
    """
    published_dir = posixpath.dirname(published_path)
    result = run(
        client,
        f"if test -f {shlex.quote(staged_path)}; then "
        f"install -d -m 0755 {shlex.quote(published_dir)} && "
        f"cp -p {shlex.quote(staged_path)} {shlex.quote(published_path)} && "
        f"chmod 0644 {shlex.quote(published_path)} && echo promoted; "
        "else echo missing; fi",
    ).strip()
    if result == "promoted":
        print(f"STAGING_PROMOTED={staged_path} -> {published_path}")
        return True
    return False


def read_remote(client: paramiko.SSHClient, remote_path: str) -> bytes:
    with client.open_sftp() as sftp:
        with sftp.open(remote_path, "rb") as stream:
            return stream.read()


def site_info(client: paramiko.SSHClient, site_root: str) -> None:
    download_root = posixpath.join(site_root, "download")
    index_path = posixpath.join(download_root, "index.html")
    html = read_remote(client, index_path).decode("utf-8", errors="replace")
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    installers = [href for href in hrefs if ".exe" in href.lower()]
    print(f"DOWNLOAD_INDEX={index_path}")
    print(f"DOWNLOAD_INDEX_BYTES={len(html.encode('utf-8'))}")
    print(f"ALL_HREFS={hrefs}")
    print(f"INSTALLER_LINKS={installers}")
    command = (
        f"find {shlex.quote(site_root)} -maxdepth 4 -type f "
        r"\( -iname '*.exe' -o -iname '*.msi' -o -iname '*.msix' -o -iname '*.zip' "
        r"-o -name 'latest.json' -o -name 'SHA256SUMS.txt' \) "
        r"-printf '%p\t%s bytes\n' 2>/dev/null | sort"
    )
    print(run(client, command), end="")
    release_root = posixpath.join(site_root, "downloads", "mindspace")
    for filename in ("latest.json", "SHA256SUMS.txt"):
        path = posixpath.join(release_root, filename)
        try:
            value = read_remote(client, path).decode("utf-8", errors="replace")
        except OSError:
            continue
        print(f"--- {path} ---")
        print(value[:4096].rstrip())
        if filename == "latest.json":
            try:
                manifest = json.loads(value)
            except json.JSONDecodeError:
                manifest = {}
            for key in ("version", "file", "size_mb", "sha256"):
                needle = str(manifest.get(key, ""))
                if not needle:
                    continue
                for match in re.finditer(re.escape(needle), html, flags=re.IGNORECASE):
                    start = max(0, match.start() - 120)
                    end = min(len(html), match.end() + 120)
                    snippet = re.sub(r"\s+", " ", html[start:end]).strip()
                    print(f"MANIFEST_MATCH_{key.upper()}={snippet}")
    for pattern in (r".{0,100}\.exe.{0,100}", r".{0,80}(?:版本|version).{0,80}"):
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            print("HTML_MATCH=" + re.sub(r"\s+", " ", match.group(0)).strip())
    for pattern in (
        r"<button\b[^>]*>.*?</button>",
        r"<a\b[^>]*(?:download|install)[^>]*>.*?</a>",
        r".{0,160}(?:download|install|onclick|fetch\().{0,240}",
    ):
        for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
            snippet = re.sub(r"\s+", " ", match.group(0)).strip()
            print("CONTROL_MATCH=" + snippet[:600])


def has_authenticode_signature(local: Path) -> bool:
    """Return whether a PE file contains a certificate table.

    This reports the presence of an Authenticode signature, not its trust state.
    Trust validation remains part of release acceptance on the signing machine.
    """
    with local.open("rb") as stream:
        if stream.read(2) != b"MZ":
            return False
        stream.seek(0x3C)
        pe_offset_bytes = stream.read(4)
        if len(pe_offset_bytes) != 4:
            return False
        pe_offset = int.from_bytes(pe_offset_bytes, "little")
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            return False
        stream.seek(pe_offset + 24)
        magic_bytes = stream.read(2)
        if len(magic_bytes) != 2:
            return False
        magic = int.from_bytes(magic_bytes, "little")
        data_directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
        if data_directory_offset is None:
            return False
        stream.seek(pe_offset + 24 + data_directory_offset + (8 * 4))
        certificate_offset = int.from_bytes(stream.read(4), "little")
        certificate_size = int.from_bytes(stream.read(4), "little")
        return certificate_offset > 0 and certificate_size > 0


def build_download_manifest(
    local: Path,
    remote_name: str,
    version: str,
    domain: str,
    *,
    published_at: str | None = None,
    signature_status: str | None = None,
) -> tuple[dict[str, object], str]:
    if not local.is_file():
        raise FileNotFoundError(local)
    if not remote_name.lower().endswith(".exe") or version not in remote_name:
        raise ValueError("installer filename must be an .exe containing the release version")
    domain = domain.strip().lower().rstrip("/")
    if domain != "douyinqijun.cn":
        raise ValueError("public download manifest domain must be douyinqijun.cn")
    digest = hashlib.sha256()
    with local.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    size = local.stat().st_size
    detected_status = "signed" if has_authenticode_signature(local) else "unsigned"
    status = signature_status or detected_status
    if status not in {"signed", "unsigned"}:
        raise ValueError("signature_status must be signed or unsigned")
    if signature_status == "signed" and detected_status != "signed":
        raise ValueError("installer was declared signed but has no PE certificate table")
    public_href = f"/downloads/mindspace/{remote_name}"
    manifest: dict[str, object] = {
        "product": "Mindspace",
        "version": version,
        "package": "windows-installer",
        "file": remote_name,
        "size_mb": round(size / (1024 * 1024), 2),
        "bytes": size,
        "sha256": sha256,
        "url": f"https://{domain}{public_href}",
        "note": "Windows x64 安装程序；环境与模型由启动器按需下载。",
        "published_at": published_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "signature_status": status,
        "channel": "stable",
    }
    return manifest, sha256


def publish_download_metadata(
    client: paramiko.SSHClient,
    local: Path,
    site_root: str,
    remote_name: str,
    version: str,
    domain: str,
) -> None:
    release_root = posixpath.join(site_root, "downloads", "mindspace")
    latest_path = posixpath.join(release_root, "latest.json")
    sums_path = posixpath.join(release_root, "SHA256SUMS.txt")
    manifest, sha256 = build_download_manifest(local, remote_name, version, domain)
    upload(client, local, release_root, remote_name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    metadata_backups: list[str] = []
    with client.open_sftp() as sftp:
        latest_payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with sftp.open(f"{latest_path}.partial", "wb") as stream:
            stream.write(latest_payload)
        sftp.chmod(f"{latest_path}.partial", 0o644)
        sums_payload = f"{sha256}  {remote_name}\n".encode()
        with sftp.open(f"{sums_path}.partial", "wb") as stream:
            stream.write(sums_payload)
        sftp.chmod(f"{sums_path}.partial", 0o644)
    for metadata_path in (latest_path, sums_path):
        exists = run(
            client,
            f"if test -f {shlex.quote(metadata_path)}; then echo yes; else echo no; fi",
        ).strip()
        if exists == "yes":
            metadata_backup = f"{metadata_path}.bak-{stamp}"
            run(client, f"cp -p {shlex.quote(metadata_path)} {shlex.quote(metadata_backup)}")
            metadata_backups.append(metadata_backup)
    run(
        client,
        " && ".join(
            [
                f"mv -f {shlex.quote(sums_path + '.partial')} {shlex.quote(sums_path)}",
                f"mv -f {shlex.quote(latest_path + '.partial')} {shlex.quote(latest_path)}",
            ]
        ),
    )
    print(f"METADATA_BACKUPS={metadata_backups}")
    print(f"PUBLIC_HREF=/downloads/mindspace/{remote_name}")
    print(f"SIGNATURE_STATUS={manifest['signature_status']}")
    print(f"PUBLISHED_AT={manifest['published_at']}")


def deploy_release(
    client: paramiko.SSHClient,
    release_root: Path,
    site_root: str,
    channel: str,
    domain: str,
) -> None:
    """Publish one signed catalog release, making the catalog visible last."""
    catalog_path = release_root / "catalog" / channel / "windows-x64.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    core_version = str(catalog["core"]["version"])
    remote_root = posixpath.join(site_root, "downloads", "mindspace")
    staging_root = posixpath.join(remote_root, "staging", core_version)

    core_public_root = posixpath.join(remote_root, "core", "releases", core_version)
    promote_staged_file(
        client,
        posixpath.join(staging_root, "core", "manifest.json"),
        posixpath.join(core_public_root, "manifest.json"),
    )
    promote_staged_file(
        client,
        posixpath.join(staging_root, "core", f"mindspace-core-{core_version}.zip"),
        posixpath.join(core_public_root, f"mindspace-core-{core_version}.zip"),
    )

    core_root = release_root / "core" / "releases" / core_version
    for local in (core_root / "manifest.json", core_root / f"mindspace-core-{core_version}.zip"):
        upload(
            client,
            local,
            posixpath.join(remote_root, "core", "releases", core_version),
            local.name,
        )

    legacy = release_root / channel / "manifest.json"
    upload(client, legacy, posixpath.join(remote_root, channel), legacy.name)

    launcher = catalog.get("launcher")
    if launcher:
        launcher_version = str(launcher["version"])
        launcher_root = release_root / "launcher" / channel
        installer = launcher_root / f"Mindspace-{launcher_version}-x64.exe"
        blockmap = Path(f"{installer}.blockmap")
        latest = launcher_root / "latest.yml"
        feed_root = posixpath.join(remote_root, "launcher", channel)
        promote_staged_file(
            client,
            posixpath.join(staging_root, "launcher", installer.name),
            posixpath.join(remote_root, installer.name),
        )
        promote_staged_file(
            client,
            posixpath.join(staging_root, "launcher", blockmap.name),
            posixpath.join(feed_root, blockmap.name),
        )
        promote_staged_file(
            client,
            posixpath.join(staging_root, "launcher", latest.name),
            posixpath.join(feed_root, latest.name),
        )

        # Only the stable channel advances the website's single public manifest.
        # The website reads this manifest at runtime and is never rewritten here.
        if channel == "stable":
            publish_download_metadata(
                client,
                installer,
                site_root,
                installer.name,
                launcher_version,
                domain,
            )
        run(client, f"install -d -m 0755 {shlex.quote(feed_root)}")
        public_installer = posixpath.join(remote_root, installer.name)
        feed_installer = posixpath.join(feed_root, installer.name)
        run(
            client,
            f"ln -f {shlex.quote(public_installer)} {shlex.quote(feed_installer)} && "
            f"chmod 0644 {shlex.quote(feed_installer)}",
        )
        upload(client, blockmap, feed_root, blockmap.name)
        upload(client, latest, feed_root, latest.name)

    remote_catalog_root = posixpath.join(remote_root, "catalog", channel)
    upload(client, catalog_path, remote_catalog_root, "windows-x64.json.next")
    remote_catalog = posixpath.join(remote_catalog_root, "windows-x64.json")
    run(
        client,
        f"mv -f {shlex.quote(remote_catalog + '.next')} {shlex.quote(remote_catalog)}",
    )
    print(f"RELEASE_VERSION={core_version}")
    print(f"REMOTE_RELEASE_ROOT={remote_root}")
    print(f"REMOTE_CATALOG={remote_catalog}")


def stage_release(
    client: paramiko.SSHClient,
    release_root: Path,
    site_root: str,
    channel: str,
) -> None:
    """Upload a complete release for verification without changing public pointers."""
    catalog_path = release_root / "catalog" / channel / "windows-x64.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    version = str(catalog["core"]["version"])
    staging_root = posixpath.join(site_root, "downloads", "mindspace", "staging", version)
    core_root = release_root / "core" / "releases" / version
    files = [
        (core_root / "manifest.json", "core/manifest.json"),
        (core_root / f"mindspace-core-{version}.zip", f"core/mindspace-core-{version}.zip"),
        (catalog_path, "catalog/windows-x64.json.pending"),
    ]
    if catalog.get("launcher"):
        launcher_root = release_root / "launcher" / channel
        installer = launcher_root / f"Mindspace-{version}-x64.exe"
        files.extend(
            [
                (installer, f"launcher/{installer.name}"),
                (Path(f"{installer}.blockmap"), f"launcher/{installer.name}.blockmap"),
                (launcher_root / "latest.yml", "launcher/latest.yml"),
            ]
        )
    for local, relative in files:
        upload(
            client,
            local,
            posixpath.join(staging_root, posixpath.dirname(relative)),
            posixpath.basename(relative),
        )
    print(f"STAGED_VERSION={version}")
    print(f"STAGING_ROOT={staging_root}")


def deploy_compatibility(
    client: paramiko.SSHClient,
    release_root: Path,
    site_root: str,
    channel: str,
) -> None:
    """Expose the current release at the legacy /updates URLs without re-uploading it."""
    catalog_path = release_root / "catalog" / channel / "windows-x64.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    core_version = str(catalog["core"]["version"])
    source_root = posixpath.join(site_root, "downloads", "mindspace")
    legacy_root = posixpath.join(site_root, "updates")

    links = (
        (
            posixpath.join(source_root, "core", "releases", core_version, "manifest.json"),
            posixpath.join(legacy_root, "core", "releases", core_version, "manifest.json"),
        ),
        (
            posixpath.join(
                source_root,
                "core",
                "releases",
                core_version,
                f"mindspace-core-{core_version}.zip",
            ),
            posixpath.join(
                legacy_root,
                "core",
                "releases",
                core_version,
                f"mindspace-core-{core_version}.zip",
            ),
        ),
        (
            posixpath.join(source_root, channel, "manifest.json"),
            posixpath.join(legacy_root, channel, "manifest.json"),
        ),
    )
    for source, target in links:
        target_dir = posixpath.dirname(target)
        run(client, f"install -d -m 0755 {shlex.quote(target_dir)}")
        run(
            client,
            f"test -f {shlex.quote(source)} && "
            f"ln -f {shlex.quote(source)} {shlex.quote(target)} && "
            f"chmod 0644 {shlex.quote(target)}",
        )
        print(f"COMPAT_LINK={target}")

    source_catalog = posixpath.join(source_root, "catalog", channel, "windows-x64.json")
    target_catalog_root = posixpath.join(legacy_root, "catalog", channel)
    target_catalog = posixpath.join(target_catalog_root, "windows-x64.json")
    next_catalog = f"{target_catalog}.next"
    run(client, f"install -d -m 0755 {shlex.quote(target_catalog_root)}")
    run(
        client,
        f"cp -p {shlex.quote(source_catalog)} {shlex.quote(next_catalog)} && "
        f"mv -f {shlex.quote(next_catalog)} {shlex.quote(target_catalog)}",
    )
    print(f"COMPAT_VERSION={core_version}")
    print(f"COMPAT_CATALOG={target_catalog}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--domain", default="")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--site-info", action="store_true")
    parser.add_argument("--publish-download-metadata", action="store_true")
    parser.add_argument("--deploy-release", action="store_true")
    parser.add_argument("--stage-release", action="store_true")
    parser.add_argument("--deploy-compatibility", action="store_true")
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--site-root", default="/usr/share/nginx/html")
    parser.add_argument("--version", default="")
    parser.add_argument("--local", type=Path)
    parser.add_argument("--remote-dir")
    parser.add_argument("--remote-name")
    args = parser.parse_args()
    client = connect(args)
    try:
        if args.inspect:
            inspect(client, args.domain)
        if args.site_info:
            site_info(client, args.site_root)
        if args.deploy_compatibility:
            if not args.release_root:
                parser.error("--deploy-compatibility requires --release-root")
            deploy_compatibility(
                client,
                args.release_root,
                args.site_root,
                args.channel,
            )
        elif args.stage_release:
            if not args.release_root:
                parser.error("--stage-release requires --release-root")
            stage_release(
                client,
                args.release_root,
                args.site_root,
                args.channel,
            )
        elif args.deploy_release:
            if not args.release_root or not args.domain:
                parser.error("--deploy-release requires --release-root and --domain")
            deploy_release(
                client,
                args.release_root,
                args.site_root,
                args.channel,
                args.domain,
            )
        elif args.publish_download_metadata:
            if not args.local or not args.remote_name or not args.version or not args.domain:
                parser.error(
                    "--publish-download-metadata requires --local, --remote-name, "
                    "--version and --domain"
                )
            publish_download_metadata(
                client,
                args.local,
                args.site_root,
                args.remote_name,
                args.version,
                args.domain,
            )
        elif args.local:
            if not args.remote_dir or not args.remote_name:
                parser.error("--remote-dir and --remote-name are required with --local")
            upload(client, args.local, args.remote_dir, args.remote_name)
    finally:
        client.close()


if __name__ == "__main__":
    main()
