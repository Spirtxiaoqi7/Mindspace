---
status: current
scope: release
last_reviewed: 2026-08-11
---

# 发布操作手册 / Release Operations

## 中文

### 发布原则与边界

所有发布命令从 `<repo>` 执行。日常业务、Prompt、RAG、TTS/ASR 编排和聊天前端变更只发布 Core 包；仅在 Electron Launcher、安装器或更新器本身变更时才加入 `-IncludeLauncher`。`<home>` 是用户运行时，不是发布源；不得上传其 `data`、`config`、`logs`、`environment`、`models`、`user-data`、`reports/` 或用户数据。

Core、Launcher 和签名目录在独立 OSS/CDN 完成配置前统一由官网 `douyinqijun.cn/downloads/mindspace` 承载。迁移 CDN 前必须完成完整在线验收；不得仅以 DNS、HTTP 200 或页面可访问作为发布成功依据。

### 生成签名更新

稳定 Core 更新：

```powershell
Set-Location <repo>
.\scripts\prepare-online-release.ps1 `
  -Version 0.4.1 `
  -Sequence 41 `
  -Channel stable `
  -Rollout 10 `
  -Title 'Mindspace 0.4.1' `
  -Notes '降低语音延迟|修复记忆写入|改进更新器'
```

Launcher 同时变更时：

```powershell
Set-Location <repo>
.\scripts\prepare-online-release.ps1 `
  -Version 0.5.0 `
  -Sequence 50 `
  -Channel stable `
  -Rollout 5 `
  -MinimumLauncher 0.5.0 `
  -IncludeLauncher
```

发布脚本必须拒绝未通过 Authenticode 验证的 Launcher。`-AllowUnsignedLauncher` 仅用于本地端到端测试，禁止用于公开频道。`Sequence` 必须永久递增，即使版本撤回也不能重复或降低。灰度可由 1、10、30、100 逐步扩大；每次改变灰度比例都以更高 Sequence 重新签名发布。

生成结果位于 `runtime\release-site\mindspace`。私钥 `runtime\update-keys\private.pem` 只留在发布机，严禁进入官网、OSS、安装包或源码仓库。

### 发布与验收

发布到本地 Web 根目录：

```powershell
Set-Location <repo>
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -WebRoot D:\www\downloads\mindspace
```

版本文件先复制，最后原子替换 `catalog/stable/windows-x64.json`，避免客户端读取半成品。通过 SSH 发布时使用密钥，不保存服务器密码：

```powershell
Set-Location <repo>
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -Remote root@your-server `
  -RemoteRoot /var/www/downloads/mindspace
```

公开前可先上传完整暂存版本；该操作不会修改官网链接、Launcher feed 或 stable 清单：

```powershell
Set-Location <repo>
.\scripts\publish-online-release-interactive.ps1 -Channel stable -StagingOnly
```

暂存路径为 `/downloads/mindspace/staging/<version>/`。完成公网文件大小、SHA-256、安装与真实运行验收后，再执行正式发布。

发布后必须执行：

```powershell
Set-Location <repo>
node .\scripts\verify-online-release.mjs --full
```

该验收必须拒绝官网 SPA 回退 HTML，并验证 JSON MIME、Ed25519 签名、Range、文件大小和 Core SHA-256。还必须确认客户端实际执行更新路径：检查启动后 5 秒和随后每 6 小时的检查、频道/Sequence/灰度资格、Launcher 优先、Core 断点续传及暂停继续、声明大小和 SHA-256，以及安装失败的 Core 回滚与 Launcher Authenticode 验证。真实业务验收须使用配置好的真实桌面 API；确定性测试只能证明管线连接。

### 服务器、容量与回滚

服务器必须提供 HTTPS、GET、HEAD 与 Range；`.partial` 续传必须返回 `206 Partial Content`。版本文件使用 `Cache-Control: public, max-age=31536000, immutable`；`catalog/*/*.json` 和 `latest.yml` 使用 `no-cache` 或不超过 60 秒缓存。服务器必须能传输 `.exe`、`.blockmap`、`.zip`、`.json` 和 `.yml`。

更新服务器只保留当前安装器、5 个 Core 版本和回滚文件时，1 GiB 可运行；建议 5–10 GiB 对象存储并启用 CDN。若客户端保留本地 ASR 和向量模型，应预留至少 12–15 GiB 磁盘。安装器体积不等于完整本地 AI 环境体积，模型与私有环境需单独计算分发流量和容量。

Core 安装失败或健康检查失败时，更新器用 rollback token 恢复上一 Core。若迁移缺陷风险出现，立即以更高 Sequence 和 `rollout=0` 停止新增更新，再发布修复版本。已迁移用户数据不得在线降级。升级前在 `<home>\data\backups\` 创建完整回滚点；只有更新器失效才使用完整目录备份，并永不删除或覆盖 `<home>\data`、`models`、`environment`、`user-data`。其中 `<home>` 是已安装运行时的 Home，通常为 `<home>`，且 `runtime_dir=home`。

## English

### Release principles and boundaries

Run all release commands from `<repo>`. Publish only a Core package for routine business, Prompt, RAG, TTS/ASR orchestration, and chat-frontend changes. Add `-IncludeLauncher` only when the Electron Launcher, installer, or updater itself changes. `<home>` is a user runtime, not a release source; do not upload its `data`, `config`, `logs`, `environment`, `models`, `user-data`, `reports/`, or any user data.

Until an independent OSS/CDN is configured, serve Core, Launcher, and signed catalogs together from `douyinqijun.cn/downloads/mindspace`. Complete full online acceptance before migrating to a CDN; DNS, HTTP 200, or a reachable page alone are not proof of a successful release.

### Generate a signed update

Stable Core update:

```powershell
Set-Location <repo>
.\scripts\prepare-online-release.ps1 `
  -Version 0.4.1 `
  -Sequence 41 `
  -Channel stable `
  -Rollout 10 `
  -Title 'Mindspace 0.4.1' `
  -Notes '降低语音延迟|修复记忆写入|改进更新器'
```

When the Launcher also changes:

```powershell
Set-Location <repo>
.\scripts\prepare-online-release.ps1 `
  -Version 0.5.0 `
  -Sequence 50 `
  -Channel stable `
  -Rollout 5 `
  -MinimumLauncher 0.5.0 `
  -IncludeLauncher
```

The release script must reject a Launcher that does not pass Authenticode verification. Use `-AllowUnsignedLauncher` only for local end-to-end testing, never for a public channel. `Sequence` must increase permanently and must not be reused or reduced even when a version is withdrawn. Increase rollout through 1, 10, 30, and 100; every rollout change is a newly signed release with a higher Sequence.

Generated output is in `runtime\release-site\mindspace`. Keep `runtime\update-keys\private.pem` only on the release machine; it must never enter the website, OSS, installer, or source repository.

### Publish and accept

Publish to a local web root:

```powershell
Set-Location <repo>
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -WebRoot D:\www\downloads\mindspace
```

Copy version files first and atomically replace `catalog/stable/windows-x64.json` last, so clients cannot see a partial release. For SSH publishing, use a key and do not store a server password:

```powershell
Set-Location <repo>
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -Remote root@your-server `
  -RemoteRoot /var/www/downloads/mindspace
```

Before public release, upload a complete staged version; this does not modify the website link, Launcher feed, or stable catalog:

```powershell
Set-Location <repo>
.\scripts\publish-online-release-interactive.ps1 -Channel stable -StagingOnly
```

The staging path is `/downloads/mindspace/staging/<version>/`. Complete public file-size, SHA-256, installation, and real-runtime acceptance before publishing formally.

After publication, run:

```powershell
Set-Location <repo>
node .\scripts\verify-online-release.mjs --full
```

This acceptance must reject website SPA-fallback HTML and verify JSON MIME, the Ed25519 signature, Range, file size, and Core SHA-256. Also confirm the client's actual update path: checks five seconds after launch and then every six hours; channel, Sequence, and rollout eligibility; Launcher priority; resumable Core downloads with pause/resume; declared size and SHA-256; Core rollback on installation failure; and Launcher Authenticode verification. Real product acceptance must use a configured real desktop API; deterministic tests prove plumbing only.

### Server, capacity, and rollback

The server must provide HTTPS, GET, HEAD, and Range; `.partial` resume must return `206 Partial Content`. Version files use `Cache-Control: public, max-age=31536000, immutable`; `catalog/*/*.json` and `latest.yml` use `no-cache` or a maximum 60-second cache. The server must be able to deliver `.exe`, `.blockmap`, `.zip`, `.json`, and `.yml`.

One GiB can operate an update server retaining the current installer, five Core versions, and rollback files; use 5–10 GiB object storage with a CDN in practice. A client retaining local ASR and vector models should reserve at least 12–15 GiB of disk. Installer size is not the size of a complete local AI environment: model and private-environment distribution traffic and capacity must be calculated separately.

If Core installation or its health check fails, the updater restores the previous Core with a rollback token. When migration-defect risk appears, immediately stop new updates with a higher Sequence and `rollout=0`, then publish a fix. Migrated user data must not be downgraded online. Before upgrade, create a complete rollback point under `<home>\data\backups\`; use a complete directory backup only if the updater fails, and never delete or overwrite `<home>\data`, `models`, `environment`, or `user-data`. Here `<home>` is the installed-runtime Home, usually `<home>`, and `runtime_dir=home`.
