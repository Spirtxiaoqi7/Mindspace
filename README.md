# Mindspace 0.9.0

Mindspace 是一个本地优先的 AI 角色、长期会话、记忆、工具调用与语音桌面系统。

Mindspace is a local-first desktop system for AI characters, long-running conversations, memory, tool use, and voice.

## 中文

### 产品能力

- V7 命格画布从角色种子生成八个方向和九十六张命签，完成十二项选择后生成标准 `chara_card_v2`。
- 近期上下文、会话摘要、事件记忆与长期 RAG 共同维持连续对话，并按角色和会话隔离。
- LangGraph 原生工具链支持 `web`、`memory` 与 `task`；工具结果只能作为数据回注，失败不得伪装成成功。
- ASR、CosyVoice、GPT-SoVITS、Qwen3-TTS 与 FFmpeg 支持已有环境复用、失败重试和按需安装。

### 开发边界

- 唯一开发目录：`A:\RAG\Mindspace-admin`
- 桌面运行目录：`A:\Mindspace`
- 权威用户数据：`A:\Mindspace\data`
- `A:\Mindspace` 不是开发工作树，用户数据、密钥、模型、日志和真实 API 报告不得提交到 Git。

### 开始维护

1. 阅读 [文档导航](docs/README.md)。
2. 阅读 [架构总览](docs/architecture/overview.md)。
3. 按 [开发流程](docs/development/workflow.md) 建立分支和提交。
4. 按 [测试门禁](docs/development/testing.md) 验证变更。
5. 桌面部署与回滚遵循 [运行手册](docs/operations/runtime.md)。

## English

### Product capabilities

- The V7 Destiny Canvas expands a character seed into eight directions and ninety-six cards, then produces a standard `chara_card_v2` after twelve selections.
- Recent context, conversation summaries, event memory, and long-term RAG maintain continuity while remaining isolated by character and session.
- The native LangGraph tool chain supports `web`, `memory`, and `task`. Tool results are injected as data, and failures must never be presented as success.
- ASR, CosyVoice, GPT-SoVITS, Qwen3-TTS, and FFmpeg support bounded local discovery, reuse, retry, and on-demand installation.

### Development boundaries

- Sole development workspace: `A:\RAG\Mindspace-admin`
- Desktop runtime: `A:\Mindspace`
- Authoritative user data: `A:\Mindspace\data`
- `A:\Mindspace` is not a development checkout. User data, secrets, models, logs, and real-API reports must never be committed.

### Start maintaining

1. Open the [documentation guide](docs/README.md).
2. Read the [architecture overview](docs/architecture/overview.md).
3. Follow the [development workflow](docs/development/workflow.md) for branches and commits.
4. Apply the [testing gates](docs/development/testing.md) before delivery.
5. Use the [runtime runbook](docs/operations/runtime.md) for desktop deployment and rollback.

## Build entry points / 构建入口

```powershell
Set-Location A:\RAG\Mindspace-admin

node scripts\sync-version.mjs --check
node scripts\verify-version-consistency.mjs
node scripts\verify-repository-policy.mjs
npm --prefix frontend run build
pwsh -File scripts\build-update.ps1 -Version 0.9.0
npm --prefix desktop run package:app
```

See [CHANGELOG.md](CHANGELOG.md) and [release-history.json](docs/release-history.json) for release facts. Historical designs are not current operating instructions.

版本事实以 [CHANGELOG.md](CHANGELOG.md) 和 [release-history.json](docs/release-history.json) 为准，历史设计不代表当前运行方式。
