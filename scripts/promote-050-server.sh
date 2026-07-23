#!/usr/bin/env bash
set -euo pipefail

SITE=/usr/share/nginx/html
ROOT="$SITE/downloads/mindspace"
STAGING="$ROOT/staging/0.5.0"
VERSION=0.5.0
STAMP="$(date +%Y%m%d-%H%M%S)"

INSTALLER="$STAGING/launcher/Mindspace-0.5.0-x64.exe"
BLOCKMAP="$STAGING/launcher/Mindspace-0.5.0-x64.exe.blockmap"
LATEST="$STAGING/launcher/latest.yml"
CORE_MANIFEST="$STAGING/core/manifest.json"
CORE_ZIP="$STAGING/core/mindspace-core-0.5.0.zip"
CATALOG="$STAGING/catalog/windows-x64.json.pending"

check_sha() {
  local expected="$1"
  local file="$2"
  test -f "$file"
  printf '%s  %s\n' "$expected" "$file" | sha256sum -c -
}

echo '[1/7] 验证暂存文件'
check_sha d8fe5bdbc22ccd500f216ef49bffccc6afebb5dd7b8263bf4d7fa688b1072b9f "$INSTALLER"
check_sha bca8171729d35c6c44b2895d03bea94b042dfe3002af4ba96ce38518a131c468 "$BLOCKMAP"
check_sha 24bdfe076d9cc43011309619ee1da861452cd54f690d1a13a5c3cde3a767ca6a "$LATEST"
check_sha f33cd4f93a87f427cc7424a2266bfab7a11fc1d28ccc1b93f667735e346de208 "$CORE_ZIP"
check_sha 3564006bb409c6d02f5c734e2fe3c6e40d40b57749f6a612b8d23b614495d2f2 "$CORE_MANIFEST"
check_sha e83773ce8d038a14e47f46f2c23b9e92404e36cae3042b929f334326341ae7a8 "$CATALOG"

echo '[2/7] 发布不可变 Core 与 Launcher 文件'
install -d -m 0755 "$ROOT/core/releases/$VERSION" "$ROOT/launcher/stable" "$ROOT/catalog/stable" "$ROOT/stable"
ln -f "$CORE_MANIFEST" "$ROOT/core/releases/$VERSION/manifest.json"
ln -f "$CORE_ZIP" "$ROOT/core/releases/$VERSION/mindspace-core-$VERSION.zip"
ln -f "$INSTALLER" "$ROOT/Mindspace-$VERSION-x64.exe"
ln -f "$ROOT/Mindspace-$VERSION-x64.exe" "$ROOT/launcher/stable/Mindspace-$VERSION-x64.exe"
ln -f "$BLOCKMAP" "$ROOT/launcher/stable/Mindspace-$VERSION-x64.exe.blockmap"
ln -f "$LATEST" "$ROOT/launcher/stable/latest.yml"
chmod 0644 "$ROOT/core/releases/$VERSION/manifest.json" "$ROOT/core/releases/$VERSION/mindspace-core-$VERSION.zip" \
  "$ROOT/Mindspace-$VERSION-x64.exe" "$ROOT/launcher/stable/Mindspace-$VERSION-x64.exe" \
  "$ROOT/launcher/stable/Mindspace-$VERSION-x64.exe.blockmap" "$ROOT/launcher/stable/latest.yml"

echo '[3/7] 生成兼容更新清单'
cat > "$ROOT/stable/manifest.json.next" <<'JSON'
{
  "schema_version": "1.0.0",
  "channel": "stable",
  "version": "0.5.0",
  "minimum_launcher": "0.3.0",
  "mandatory": false,
  "published_at": "2026-07-21T13:15:31.446Z",
  "release_notes": "首页改为状态概览与四个可伸缩分类\n基础环境按真实依赖顺序安装并复用已就绪组件\n下载失败显示错误码、阶段、操作编号并可导出脱敏诊断报告\n更新保留环境、模型、数据与缓存\n包含 GPT-SoVITS V4 长离与八重神子韵律和启动修复",
  "package": {
    "url": "https://douyinqijun.cn/downloads/mindspace/core/releases/0.5.0/mindspace-core-0.5.0.zip",
    "sha256": "f33cd4f93a87f427cc7424a2266bfab7a11fc1d28ccc1b93f667735e346de208",
    "size": 8176506,
    "format": "zip"
  },
  "signature": {
    "algorithm": "ed25519",
    "value": "AWR1+z8RDlNQCJqkn0Gy+HnceY/7UqaJG3H5tDTjlji9p2GnN4IEFtuiW3DuxfNVvokdTTYV8wcZhlBlXzYlDQ=="
  }
}
JSON
chmod 0644 "$ROOT/stable/manifest.json.next"

echo '[4/7] 准备官网元数据与下载页'
cat > "$ROOT/latest.json.next" <<'JSON'
{
  "product": "Mindspace",
  "version": "0.5.0",
  "package": "windows-installer",
  "file": "Mindspace-0.5.0-x64.exe",
  "size_mb": 270.48,
  "bytes": 283621635,
  "sha256": "d8fe5bdbc22ccd500f216ef49bffccc6afebb5dd7b8263bf4d7fa688b1072b9f",
  "url": "https://douyinqijun.cn/downloads/mindspace/Mindspace-0.5.0-x64.exe",
  "note": "Windows x64 安装程序；环境与模型由启动器按需下载。"
}
JSON
printf '%s  %s\n' d8fe5bdbc22ccd500f216ef49bffccc6afebb5dd7b8263bf4d7fa688b1072b9f Mindspace-0.5.0-x64.exe > "$ROOT/SHA256SUMS.txt.next"

INDEX="$SITE/download/index.html"
python3 - "$INDEX" "$INDEX.next" <<'PY'
import re
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
html = source.read_text(encoding="utf-8")
old_links = re.findall(r'href=["\']([^"\']*\.(?:exe|zip)(?:\?[^"\']*)?)["\']', html, flags=re.I)
updated, count = re.subn(
    r'(?i)(href=["\'])[^"\']*\.(?:exe|zip)(?:\?[^"\']*)?(["\'])',
    lambda match: f'{match.group(1)}/downloads/mindspace/Mindspace-0.5.0-x64.exe{match.group(2)}',
    html,
)
if not count:
    raise SystemExit("download page has no installer link")
for link in old_links:
    for version in re.findall(r'(?<!\d)(\d+\.\d+\.\d+)(?!\d)', link):
        updated = re.sub(rf'(?i)(?<![\d.])v?{re.escape(version)}(?![\d.])', lambda m: ('v' if m.group(0).lower().startswith('v') else '') + '0.5.0', updated)
target.write_text(updated, encoding="utf-8")
print(f"PAGE_LINK_REPLACEMENTS={count}")
PY
chmod 0644 "$INDEX.next" "$ROOT/latest.json.next" "$ROOT/SHA256SUMS.txt.next"

echo '[5/7] 备份并原子切换官网与 stable'
cp -p "$INDEX" "$INDEX.bak-$STAMP"
for file in "$ROOT/latest.json" "$ROOT/SHA256SUMS.txt" "$ROOT/stable/manifest.json" "$ROOT/catalog/stable/windows-x64.json"; do
  if test -f "$file"; then cp -p "$file" "$file.bak-$STAMP"; fi
done
cp -p "$CATALOG" "$ROOT/catalog/stable/windows-x64.json.next"
mv -f "$INDEX.next" "$INDEX"
mv -f "$ROOT/latest.json.next" "$ROOT/latest.json"
mv -f "$ROOT/SHA256SUMS.txt.next" "$ROOT/SHA256SUMS.txt"
mv -f "$ROOT/stable/manifest.json.next" "$ROOT/stable/manifest.json"
mv -f "$ROOT/catalog/stable/windows-x64.json.next" "$ROOT/catalog/stable/windows-x64.json"

echo '[6/7] 更新旧版 /updates 兼容入口'
UPDATES="$SITE/updates"
install -d -m 0755 "$UPDATES/core/releases/$VERSION" "$UPDATES/stable" "$UPDATES/catalog/stable"
ln -f "$ROOT/core/releases/$VERSION/manifest.json" "$UPDATES/core/releases/$VERSION/manifest.json"
ln -f "$ROOT/core/releases/$VERSION/mindspace-core-$VERSION.zip" "$UPDATES/core/releases/$VERSION/mindspace-core-$VERSION.zip"
ln -f "$ROOT/stable/manifest.json" "$UPDATES/stable/manifest.json"
ln -f "$ROOT/catalog/stable/windows-x64.json" "$UPDATES/catalog/stable/windows-x64.json"
chmod 0644 "$UPDATES/core/releases/$VERSION/manifest.json" "$UPDATES/core/releases/$VERSION/mindspace-core-$VERSION.zip" \
  "$UPDATES/stable/manifest.json" "$UPDATES/catalog/stable/windows-x64.json"

echo '[7/7] 本机服务端验收'
python3 - <<'PY'
import json
from pathlib import Path

root = Path('/usr/share/nginx/html/downloads/mindspace')
catalog = json.loads((root / 'catalog/stable/windows-x64.json').read_text(encoding='utf-8-sig'))
latest = json.loads((root / 'latest.json').read_text(encoding='utf-8-sig'))
assert catalog['core']['version'] == '0.5.0'
assert catalog['launcher']['version'] == '0.5.0'
assert latest['version'] == '0.5.0'
assert (root / 'Mindspace-0.5.0-x64.exe').stat().st_size == 283621635
print('STABLE_VERSION=0.5.0')
print('RELEASE_STATUS=published')
PY
