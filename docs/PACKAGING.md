> 状态：current。执行前以 docs/INDEX.md 和 0.9.0 当前权威文档为准。

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
4. 当前在线安装器只携带 Launcher、运行时清单和 Core 引导包。PowerShell 7、MinGit、uv 与
   Python 3.11 由首次引导第 02 步按需下载到 Mindspace Home 的 `environment`，支持断点续传；
   它们不写系统 PATH，也不进入系统 Python。
5. 声音能力整体可选；启动器按用户选择安装或切换 GPT-SoVITS、CosyVoice、Qwen3 或云端
   Provider，ASR 和向量模型同样按需独立下载。
6. 核心更新使用 Ed25519 签名清单与 SHA-256 校验，安装失败或健康检查失败时自动回滚。

运行时下载的官网入口只承担控制面：`douyinqijun.cn` 返回到权威上游的短期 `302`，不代理
响应体，也不在官网服务器保存第三方运行时和语音模型。客户端的“国内优先”策略在该入口失败
后自动切换清单中的官方 URL，且使用有限重试；下载进度必须显示实际域名，不能把基础链总进度
误标成单个 PowerShell 文件的进度。

已经发布且内置旧 `downloads.douyinqijun.cn` 清单的 Launcher 不会自动读取新的主域名入口。
兼容旧包必须完成该子域名的 DNS 与 TLS 配置，或者发布含新签名运行时清单的 Launcher 热修复；
仅修改主域名 Nginx 无法改变旧客户端内置地址。

这样主程序、业务服务、前端和模型可以分别升级，避免每次模型更新都重发完整安装包。

### 在线包与历史全量包体积

`0.5.39` 安装器曾携带解压后约 `528.95MB` 的 PowerShell、MinGit、Python 和 uv，NSIS 文件
为 `270.72MB`。从 `0.5.40` 起，`resources\runtime\bundled` 改为首次引导下载，安装器缩小
为约 `112.8MB`；`0.5.49` 仍内置约 `8.05MB` 的当前 Core ZIP，因此安装包变小不代表 Core
或语音逻辑被删除。

公开发行时建议区分：

- 在线安装器：当前分层方案，基础环境和声音按需下载；
- 基础离线安装器：另行携带四个基础运行时，但仍不内置大型 ASR/TTS 模型。

两者应使用不同文件名、体积说明和校验值，禁止用同一下载入口静默互换。

当前命令：

```powershell
node .\scripts\generate-update-key.mjs
pwsh -File .\scripts\build-update.ps1 -Version 0.5.8 -BaseUrl https://updates.example.com/stable
pwsh -File .\scripts\test-update-e2e.ps1
```

私钥只保存在 `runtime\update-keys`，不得上传；安装包只包含公钥。Launcher 可配置 HTTPS `manifest.json`，每 6 小时自动检查一次，也可手动检查、下载、安装和回滚。

面向公开用户分发的 NSIS 安装器还应使用 Authenticode 代码签名证书。Ed25519 更新签名用于验证 Mindspace 更新目录和 Core ZIP，不能替代 Windows 对安装器发布者的代码签名。
