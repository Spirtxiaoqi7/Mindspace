# 封装与分包方案

## 分层边界

| 包 | 内容 | 是否可独立替换 |
|---|---|---|
| `mindspace_graph` | 图、模型、策略、端口 | 核心 |
| `adapters` | JSON、检索、模型、审计 | 是 |
| `service.py` | 产品容器与会话服务 | 是 |
| `audio.py` | TTS/ASR 供应商适配 | 是 |
| `api.py` | HTTP/SSE/OpenAPI | 是 |
| `web/` | 无构建静态前端 | 是 |
| `runtime/` | 用户数据与日志 | 始终外置 |

## Wheel

```powershell
pwsh -File .\scripts\build.ps1
```

Hatch 将 `src/mindspace_graph/web` 强制包含进 wheel。安装后通过 `mindspace-server` 启动。

## 便携 ZIP

```powershell
pwsh -File .\scripts\package.ps1
```

ZIP 包含：

- 应用 wheel
- `portable-start.ps1`
- `.env.example`
- README

解压后执行：

```powershell
pwsh -File .\portable-start.ps1 -OpenBrowser
```

脚本在包内创建独立 `.venv` 和 `runtime`，不会写入系统 Python。

## Docker

```powershell
docker compose up --build
```

容器仅暴露到本机 `127.0.0.1:8765`，数据挂载到 `./runtime:/data`。

## 零环境桌面封装

Electron Launcher 同时承担窗口、系统托盘、签名更新与私有运行时引导：

1. 桌面主进程分配本地端口并启动 `mindspace-server` 子进程。
2. 健康检查通过后加载本地 URL。
3. 退出前先调用 interrupt，再优雅终止服务。
4. PowerShell 7、MinGit、uv 与 Python 3.11 作为预置引导运行时进入安装包，统一部署到 `%LOCALAPPDATA%\Mindspace\environment`。
5. 上线默认走 SiliconFlow TTS API，本地 TTS 仅为可选组件；ASR 和向量模型按需独立下载。
6. 核心更新使用 Ed25519 签名清单与 SHA-256 校验，安装失败或健康检查失败时自动回滚。

这样主程序、业务服务、前端和模型可以分别升级，避免每次模型更新都重发完整安装包。

当前命令：

```powershell
node .\scripts\generate-update-key.mjs
pwsh -File .\scripts\build-update.ps1 -Version 0.5.7 -BaseUrl https://updates.example.com/stable
pwsh -File .\scripts\test-update-e2e.ps1
```

私钥只保存在 `runtime\update-keys`，不得上传；安装包只包含公钥。Launcher 可配置 HTTPS `manifest.json`，每 6 小时自动检查一次，也可手动检查、下载、安装和回滚。

面向公开用户分发的 NSIS 安装器还应使用 Authenticode 代码签名证书。Ed25519 更新签名用于验证 Mindspace 更新目录和 Core ZIP，不能替代 Windows 对安装器发布者的代码签名。
