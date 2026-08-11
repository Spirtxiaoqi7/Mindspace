---
status: current
scope: source, portable, and Core-only deployment without an installer
last_reviewed: 2026-08-11
---

# 非 EXE 部署 / Non-EXE Deployment

## 中文

Mindspace 不要求固定盘符。本文用 `<repo>` 表示源码仓库根目录，用 `<home>` 表示用户选择的可写运行目录。无论采用哪种方式，都应将 `MINDSPACE_HOME` 指向 `<home>`，并保护 `<home>\data`、`config`、`logs`、`models` 和 `environment`。

### 方案一：源码运行

适合开发和调试。需要 Python 3.11、uv、Node.js 与 npm。

```powershell
Set-Location <repo>
uv sync --frozen --extra dev
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
$env:MINDSPACE_HOME = '<home>'
uv run mindspace-server
```

另开终端运行桌面壳：

```powershell
Set-Location <repo>\desktop
$env:MINDSPACE_HOME = '<home>'
npm run dev
```

### 方案二：免安装目录版

适合希望保留桌面体验但不运行安装器的用户。构建结果是可整体移动的 unpacked 目录，不写注册表；用户数据仍放在独立 `<home>` 中。

```powershell
Set-Location <repo>
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run dist
```

分发 `desktop\release\win-unpacked` 的完整内容。启动前设置 `MINDSPACE_HOME`，或在 Launcher 首次启动时选择 Home。升级时替换应用目录，不覆盖 `<home>`。

### 方案三：Core-only

适合已有浏览器壳、反向代理或二次集成。它不包含 Electron Launcher。

```powershell
Set-Location <repo>
uv sync --frozen
$env:MINDSPACE_HOME = '<home>'
uv run mindspace-server
```

仅监听本机时保持默认绑定；需要局域网访问时，必须自行配置访问控制、可信反向代理和防火墙。不要把 API 密钥或用户数据库放入静态 Web 目录。

### 共同验收

1. 健康接口报告的 `runtime_dir`、数据目录和配置目录均派生自同一个 `<home>`。
2. 前端通过受支持的 Core 地址和认证头访问，不能依赖构建机端口或路径。
3. 普通聊天、流式聊天、角色卡、记忆和联网工具各完成一次真实运行验收。
4. 删除或替换应用目录后，重新指向同一个 `<home>` 仍能恢复用户数据。

## English

Mindspace does not require a fixed drive letter. This guide uses `<repo>` for the source repository root and `<home>` for a user-selected writable runtime directory. In every setup, point `MINDSPACE_HOME` to `<home>` and protect `<home>\data`, `config`, `logs`, `models`, and `environment`.

### Option 1: Run from source

Use this for development and debugging. Python 3.11, uv, Node.js, and npm are required.

```powershell
Set-Location <repo>
uv sync --frozen --extra dev
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
$env:MINDSPACE_HOME = '<home>'
uv run mindspace-server
```

In another terminal, start the desktop shell:

```powershell
Set-Location <repo>\desktop
$env:MINDSPACE_HOME = '<home>'
npm run dev
```

### Option 2: Portable unpacked desktop

Use this when a desktop experience is required without running an installer. The result is a movable unpacked directory and does not require registry installation; user data remains in a separate `<home>`.

```powershell
Set-Location <repo>
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run dist
```

Distribute the complete `desktop\release\win-unpacked` directory. Set `MINDSPACE_HOME` before launch or select a Home on first launch. Replace only the application directory during upgrades; never overwrite `<home>`.

### Option 3: Core only

Use this for an existing browser shell, reverse proxy, or secondary integration. Electron Launcher is not included.

```powershell
Set-Location <repo>
uv sync --frozen
$env:MINDSPACE_HOME = '<home>'
uv run mindspace-server
```

Keep the default loopback binding for local-only access. LAN exposure requires explicit access control, a trusted reverse proxy, and firewall rules. Never place API keys or user databases in a static Web directory.

### Shared acceptance checks

1. The health endpoint reports a runtime root, data directory, and configuration directory derived from the same `<home>`.
2. The frontend uses a supported Core address and authentication header, never a build-machine port or path.
3. Complete one real run each for normal chat, streaming chat, character cards, memory, and web tools.
4. After replacing or deleting the application directory, pointing to the same `<home>` restores user data.
