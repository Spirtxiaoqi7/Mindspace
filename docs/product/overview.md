---
status: current
scope: product overview for ordinary users and new developers
last_reviewed: 2026-08-11
---

# Mindspace 产品总览 / Product Overview

## 中文

Mindspace 是 Windows 桌面 AI 应用，用于长期角色陪伴、连续对话与个人知识管理。用户可以创建或选择角色，进行流式文字聊天，按需使用实时语音，并把自己的资料加入可检索知识库。系统会在明确的用户事实、角色卡和当前会话之间保持边界，让角色表现、关系与上下文可以持续但仍可检查和编辑。

对新开发者而言，桌面壳负责交互，Core 负责 API、对话工作流、检索、存储与运行任务。普通聊天通过可恢复的持久运行和 SSE 交付；检索将本地知识、结构化记忆和近期对话分层使用，而不是把所有历史无差别塞入模型。

### 数据归属与联网边界

- 用户档案、角色卡、会话、记忆、知识库和媒体属于当前 Windows 用户，保存在私有运行数据根，而非安装包或源码目录。
- 安装、Core 更新和应用回滚不应覆盖这些用户数据。
- 使用云端 LLM 或 TTS 时，只有本轮所需的 Prompt 或待朗读文本会发往用户选择的服务商；API 密钥不进入模型 Prompt。

### 可恢复行为

- 流式对话可按运行标识和事件位置恢复；中断会停止后续有副作用的写入，而不会把失败伪装成成功。
- 更新先在 staging 部署并校验完整性；新核心无法启动时恢复上一版本，同时保留用户数据和运行环境。
- 数据问题通过备份、原子写入和受控迁移处理；系统不得以自动清空、重建用户资料来掩盖故障。

角色创建和命运抽签的具体规则见《角色与命运》；角色长期设定采用 V2 卡。旧版、非 V2 的角色档案不再是受支持的导入或自动迁移输入，应作为用户自行保留的历史资料，而不是交给当前产品直接使用。

## English

Mindspace is a Windows desktop AI application for long-term character companionship, continuous conversations, and personal knowledge management. Users can create or select characters, chat with streamed text, use real-time voice when desired, and add their own material to a searchable knowledge base. The product keeps explicit boundaries among user-stated facts, character cards, and the current conversation so that character behavior, relationships, and context can persist while remaining inspectable and editable.

For new developers, the desktop shell owns interaction while Core owns the API, conversation workflow, retrieval, storage, and runtime jobs. Ordinary chats are delivered through resumable durable runs and SSE. Retrieval uses local knowledge, structured memory, and recent conversation in separate layers instead of indiscriminately placing all history in the model context.

### Data ownership and network boundary

- User profiles, character cards, sessions, memories, knowledge bases, and media belong to the current Windows user. They are stored in a private runtime-data root, not in the installer or source tree.
- Installation, Core updates, and application rollbacks must not overwrite that user data.
- When a cloud LLM or TTS service is used, only the prompt or text needed for the current turn is sent to the provider selected by the user. API keys never enter the model prompt.

### Recoverable behavior

- A streamed chat can resume from its run identifier and event position. Interrupting it stops later side-effecting writes and must never present failure as success.
- Updates are staged and integrity-checked. If a new Core cannot start, the previous version is restored while user data and the runtime environment remain intact.
- Data issues are handled with backups, atomic writes, and controlled migrations. The product must not hide a failure by automatically clearing or recreating user material.

Character creation and destiny-draw rules are described in *Characters and Destiny*. Long-term character definition uses the V2 card. Older, non-V2 character archives are no longer supported as import or automatic-migration input; users should retain them as their own historical material rather than expect the current product to use them directly.
