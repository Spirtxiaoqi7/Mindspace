---
status: current
scope: packaging
last_reviewed: 2026-08-11
---

# 封装操作手册 / Packaging Operations

## 中文

### 封装边界

所有封装从 `A:\RAG\Mindspace-admin` 执行。`mindspace_graph` 是图、模型、策略和端口核心；`adapters`、`service.py`、`audio.py`、`api.py` 与 `web/` 可独立替换。`runtime/` 以及已安装 Home 中的 `data`、`config`、`logs`、`environment`、`models` 和 `user-data` 始终外置，不得打入 wheel、Core ZIP 或发布输入。

`A:\Mindspace` 仅为桌面运行时。它的 `runtime_dir=home` 布局以 Home 为根，用户可写内容必须留在该 Home 中；不得把已安装目录当作源代码检出或由构建过程读取用户数据。

### Wheel

```powershell
Set-Location A:\RAG\Mindspace-admin
pwsh -File .\scripts\build.ps1
```

Hatch 必须将 `src/mindspace_graph/static/app` 包含进 wheel。安装后使用 `mindspace-server` 启动。

### 便携 ZIP

```powershell
Set-Location A:\RAG\Mindspace-admin
pwsh -File .\scripts\package.ps1
```

ZIP 包含应用 wheel、`portable-start.ps1`、`.env.example` 和 README。解压后执行：

```powershell
pwsh -File .\portable-start.ps1 -OpenBrowser
```

包内脚本创建独立 `.venv` 和 `runtime`，不写入系统 Python。

### Docker

```powershell
Set-Location A:\RAG\Mindspace-admin
docker compose up --build
```

容器仅暴露到 `127.0.0.1:8765`，并将数据挂载为 `./runtime:/data`。

### 零环境桌面封装

Electron Launcher 承担窗口、系统托盘、签名更新与私有运行时引导。它分配本地端口并启动服务，健康检查后加载本地 URL，退出前 interrupt 并优雅终止服务。在线安装器只携带 Launcher、运行时清单和 Core 引导包；PowerShell 7、MinGit、uv 与 Python 3.11 在首次引导时按需下载到 Mindspace Home 的 `environment`，支持断点续传，不写系统 PATH，也不进入系统 Python。

语音能力整体可选，GPT-SoVITS、CosyVoice、Qwen3、云端 Provider、ASR 和向量模型按用户选择独立下载。Core 更新必须使用 Ed25519 签名清单和 SHA-256 校验；安装或健康检查失败时必须自动回滚。

官网运行时入口仅作为控制面，返回到权威上游的短期 `302`，不代理响应体，也不保存第三方运行时或语音模型。客户端可在国内优先入口失败后切换清单中的官方 URL，并采用有限重试。下载进度必须显示实际域名，基础链总进度不得标示为单个 PowerShell 文件进度。

旧 Launcher 内置旧下载入口时，必须完成旧入口的 DNS/TLS 兼容，或发布含新签名运行时清单的 Launcher 热修复；仅修改新主域名的服务器配置不会改变旧客户端内置地址。

### 更新构建与签名

```powershell
Set-Location A:\RAG\Mindspace-admin
node .\scripts\generate-update-key.mjs
pwsh -File .\scripts\build-update.ps1 -Version 0.5.8 -BaseUrl https://updates.example.com/stable
pwsh -File .\scripts\test-update-e2e.ps1
```

私钥只保存在 `runtime\update-keys`，不得上传；安装包只包含公钥。Ed25519 签名只验证 Mindspace 更新目录和 Core ZIP，不能替代面向公开用户的 NSIS 安装器所需的 Windows Authenticode 代码签名。

在线安装器与基础离线安装器必须使用不同文件名、体积说明和校验值，禁止以同一下载入口静默互换。在线包可按需下载基础环境和声音；基础离线包可携带四个基础运行时，但不得默认内置大型 ASR/TTS 模型。

## English

### Packaging boundaries

Run all packaging from `A:\RAG\Mindspace-admin`. `mindspace_graph` is the core for graphs, models, policies, and ports; `adapters`, `service.py`, `audio.py`, `api.py`, and `web/` may be replaced independently. `runtime/`, together with `data`, `config`, `logs`, `environment`, `models`, and `user-data` in the installed Home, is always external and must not enter a wheel, Core ZIP, or release input.

`A:\Mindspace` is desktop runtime only. Its `runtime_dir=home` layout uses Home as the root, and user-writable content must remain in that Home. Do not treat the installed directory as a source checkout or read its user data during builds.

### Wheel

```powershell
Set-Location A:\RAG\Mindspace-admin
pwsh -File .\scripts\build.ps1
```

Hatch must include `src/mindspace_graph/static/app` in the wheel. Start the installed package with `mindspace-server`.

### Portable ZIP

```powershell
Set-Location A:\RAG\Mindspace-admin
pwsh -File .\scripts\package.ps1
```

The ZIP contains the application wheel, `portable-start.ps1`, `.env.example`, and README. After extraction, run:

```powershell
pwsh -File .\portable-start.ps1 -OpenBrowser
```

The in-package script creates an isolated `.venv` and `runtime` without writing to system Python.

### Docker

```powershell
Set-Location A:\RAG\Mindspace-admin
docker compose up --build
```

The container exposes only `127.0.0.1:8765` and mounts data as `./runtime:/data`.

### Zero-environment desktop packaging

The Electron Launcher owns the window, system tray, signed updates, and private-runtime bootstrap. It allocates a local port and starts the service, loads the local URL after the health check, and interrupts then gracefully terminates the service on exit. The online installer carries only the Launcher, runtime manifest, and Core bootstrap package. PowerShell 7, MinGit, uv, and Python 3.11 are downloaded on demand during first bootstrap into `environment` under Mindspace Home, with resumable downloads; they do not write the system PATH or enter system Python.

Voice is optional as a whole. GPT-SoVITS, CosyVoice, Qwen3, cloud providers, ASR, and vector models download independently according to the user's choice. Core updates must use an Ed25519-signed manifest and SHA-256 verification; installation or health-check failures must roll back automatically.

The official runtime entry point is control-plane only: it returns a short-lived `302` to an authoritative upstream, does not proxy response bodies, and does not store third-party runtimes or voice models. After a China-preferred endpoint fails, the client may switch to the official URL in the manifest with limited retries. Download progress must show the actual domain and must not label overall bootstrap progress as the progress of one PowerShell file.

When an older Launcher embeds an older download endpoint, retain DNS/TLS compatibility for that endpoint or ship a Launcher hotfix with a newly signed runtime manifest. Changing only the server configuration for a new main domain cannot alter the endpoint embedded in old clients.

### Update build and signing

```powershell
Set-Location A:\RAG\Mindspace-admin
node .\scripts\generate-update-key.mjs
pwsh -File .\scripts\build-update.ps1 -Version 0.5.8 -BaseUrl https://updates.example.com/stable
pwsh -File .\scripts\test-update-e2e.ps1
```

Keep the private key only in `runtime\update-keys`; never upload it. The installer contains only the public key. Ed25519 verifies the Mindspace update catalog and Core ZIP only; it does not replace Windows Authenticode code signing required for an NSIS installer distributed to the public.

The online installer and base offline installer must have distinct filenames, size descriptions, and checksums. Do not silently interchange them at one download URL. The online package may download the base environment and voice on demand; the base offline package may carry the four base runtimes but must not include large ASR/TTS models by default.
