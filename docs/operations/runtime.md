---
status: current
scope: runtime
last_reviewed: 2026-08-11
---

# 运行操作手册 / Runtime Operations

## 中文

### 适用范围与边界

- 可编辑的唯一开发源是 `<repo>`。所有开发、构建和发布命令都从该目录执行。
- `<home>` 是已安装的桌面运行时和用户工作区，不是开发检出目录；不得在其中修改业务代码、脚本或作为发布输入。
- 已安装运行时以 `home` 为运行时根，即 `runtime_dir=home`。部署时 `home` 应解析为 `<home>`（或用户明确配置的等效 Mindspace Home），不得回落为构建机路径。
- 运行时目录布局如下：`<home>\data` 保存用户数据和持久化运行状态，`<home>\config` 保存运行时配置，`<home>\logs` 保存日志；`environment`、`models`、`user-data` 与更新备份也必须留在 Home 内。发布包和 Core 更新不得包含这些用户可写目录。

### 本地开发

在开发源目录执行：

```powershell
Set-Location <repo>
uv sync --frozen --extra dev
uv run mindspace-server
```

前端和桌面端分别使用各自 lockfile 执行 `npm ci`。端口只可由 `config/service-ports.json`、受支持的环境变量或桌面设置桥解析；不得在脚本中复制旧端口常量。

### 已安装运行时

Launcher 分配本地端口并启动 `mindspace-server` 子进程，健康检查成功后才加载本地 URL。退出时先发出 interrupt，再优雅终止服务。服务只能通过私有 PATH 和绝对路径环境变量获得运行时：`MINDSPACE_HOME`、`MINDSPACE_RUNTIME_DIR`、`MINDSPACE_MODEL_ROOT`、`MINDSPACE_PWSH`、`MINDSPACE_UV`、`MINDSPACE_CORE_PYTHON`。

私有 PowerShell 7、MinGit、uv 与 Python 3.11 应安装在 `<home>\environment`，不写系统 PATH，也不使用系统 Python。基础 Core、向量模型和用户数据不是可卸载组件；可选语音与模型组件需保留共享依赖保护。NVIDIA 驱动是唯一不能私有部署的系统组件；缺失时只禁用本地语音，不得阻塞文字聊天、RAG、人物卡或云端 TTS。

### 故障定位顺序

1. 检查 durable run 的终态和 provider attempts。
2. 检查当前轮是否实际产生工具 request 或 attempt；未产生时 UI 不得显示工具卡。
3. 检查会话 ID、回合 ID、摘要 ID 是否一致；禁止以全局临时文件恢复摘要。
4. 检查附件、引用和互动标签是否随同一请求进入并被持久化。
5. 命格失败时检查前 6 项和后 6 项独立状态，只重试失败半批。
6. 检查健康接口报告的版本、`runtime_dir` 和 Home 派生的 `data`、`config`、`logs` 路径是否一致。

不得从桌面普通 `settings.json` 复制密钥到测试运行时，不得在 CI 调用真实 provider，不得手写 bootstrap manifest，也不得修改签名 runtime manifest 后继续使用旧签名。`reports/` 和用户数据不是发布输入。

### 备份与回滚

迁移前先在 `<home>\data\backups\` 建立明确命名的备份。角色迁移必须先投影旧 AI 档案、运行状态、头像配置和会话 JSON；SQLite 事务成功后才写入完成标记。文件投影不是权威数据，写入失败须记录 projection failure，并可从 SQLite 重建；迁移前备份不得自动删除。

Core 更新先验证签名、SHA-256 和准确字节数，再切换目录。健康检查失败时用更新器 rollback token 恢复上一 Core。已迁移用户数据不得在线降级；迁移缺陷应以更高 Sequence、`rollout=0` 暂停新增更新，并发布修复版本。只有更新器无法执行时才使用完整目录备份，且不得删除或覆盖 `<home>\data`、`models`、`environment` 或 `user-data`。

## English

### Scope and boundaries

- The sole editable development source is `<repo>`. Run all development, build, and release commands from this directory.
- `<home>` is the installed desktop runtime and user workspace, not a development checkout. Do not modify product code or scripts there, and do not use it as release input.
- The installed runtime uses `home` as its runtime root: `runtime_dir=home`. At deployment, `home` must resolve to `<home>` (or an explicitly configured equivalent Mindspace Home), never to a build-machine path.
- The runtime layout is: `<home>\data` for user data and durable runtime state, `<home>\config` for runtime configuration, and `<home>\logs` for logs. `environment`, `models`, `user-data`, and update backups must also remain inside Home. Release packages and Core updates must not include these user-writable directories.

### Local development

Run from the development source:

```powershell
Set-Location <repo>
uv sync --frozen --extra dev
uv run mindspace-server
```

Run `npm ci` with the respective lockfile for the frontend and desktop projects. Resolve ports only through `config/service-ports.json`, supported environment variables, or the desktop settings bridge; do not copy legacy port constants into scripts.

### Installed runtime

The Launcher allocates a local port and starts the `mindspace-server` child process, loading the local URL only after its health check succeeds. On exit, it sends interrupt first and then terminates the service gracefully. The service may obtain its runtime only through the private PATH and absolute-path variables: `MINDSPACE_HOME`, `MINDSPACE_RUNTIME_DIR`, `MINDSPACE_MODEL_ROOT`, `MINDSPACE_PWSH`, `MINDSPACE_UV`, and `MINDSPACE_CORE_PYTHON`.

Install private PowerShell 7, MinGit, uv, and Python 3.11 in `<home>\environment`; do not write the system PATH or use system Python. Base Core, the vector model, and user data are not uninstallable components; optional voice and model components must preserve shared-dependency protection. The NVIDIA driver is the only system component that cannot be deployed privately. If it is absent, disable only local voice, not text chat, RAG, character cards, or cloud TTS.

### Diagnostic order

1. Check the durable-run terminal state and provider attempts.
2. Check whether the current turn actually created a tool request or attempt; if not, the UI must not show a tool card.
3. Check that session ID, turn ID, and summary ID agree; do not restore summaries through a global temporary file.
4. Check that attachments, citations, and interaction tags entered the same request and were persisted.
5. For fate-profile failures, inspect the first six and last six independent states, then retry only the failed half.
6. Check that the health endpoint reports consistent version, `runtime_dir`, and Home-derived `data`, `config`, and `logs` paths.

Do not copy secrets from ordinary desktop `settings.json` into test runtimes, call real providers in CI, hand-author bootstrap manifests, or reuse an old signature after changing a signed runtime manifest. `reports/` and user data are not release inputs.

### Backup and rollback

Before migration, create a clearly named backup in `<home>\data\backups\`. Character migration must first project legacy AI profiles, runtime state, avatar configuration, and session JSON; write the completion marker only after the SQLite transaction succeeds. File projections are not authoritative data. On a write failure, record a projection failure and allow reconstruction from SQLite; do not automatically delete the pre-migration backup.

Before a Core switch, verify the signature, SHA-256, and exact byte count. If the health check fails, restore the previous Core with the updater rollback token. Migrated user data must not be downgraded online; for a migration defect, halt new updates with a higher Sequence and `rollout=0`, then release a fix. Use a complete directory backup only if the updater cannot run, and never delete or overwrite `<home>\data`, `models`, `environment`, or `user-data`.
