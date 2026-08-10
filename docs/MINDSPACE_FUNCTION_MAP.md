# Mindspace 0.8.3 功能图

> 状态：current。

| 功能 | 权威入口 | 持久状态 | 主要门禁 |
|---|---|---|---|
| V7 命格 | destiny API + V7 画布 | journey、半批状态、selection、V2 | 8 方向、6+6、失败半批重试 |
| 角色库 | V2 character API | chara_card_v2、revision | 导入导出、删除、迁移 |
| 聊天 | chat/chat stream | durable run、turn、summary | 引用、附件、互动、重连 |
| Provider | 本地设置桥 | provider config、attempt | 模型列表、失败尝试、密钥边界 |
| 工具 | 原生短指令 | tool attempt、result summary | null/attempt、幂等、失败事实 |
| 桌面 | preload + service config | 本地设置、端口、更新状态 | bridge、端口覆盖、更新签名 |
| 语音 | ASR/TTS workers | 会话音频状态 | 模型目录、音色清单镜像 |

命格的“生成命签”是一个产品阶段、两个模型调用。前 6 类和后 6 类都通过才进入下一阶段，这不是旧的一次 96 卡调用，也不是新增中间页面。

## 拆分模块映射

| 产品域 | 后端 | Web | Desktop |
|---|---|---|---|
| 聊天/run | `api_routes/chat_runs.py`、`conversation_runs.py`、`service.py` | `chat/useConversation.ts`、`chat/MessageList.tsx` | Core service supervision |
| 工具/provider | `native_tools.py`、`tool_chain.py`、`adapters/openai_compatible.py` | `chat/ExecutionInspector.tsx` | provider 设置桥 |
| 角色/V2 | `api_routes/characters_cards.py`、`characters.py`、`character_card.py` | `characters/CharacterExperience.tsx` | 窄 preload IPC |
| V7 命格 | `api_routes/destiny_routes.py`、`destiny.py` | `DestinyCanvas.tsx` | 无业务实现 |
| 设置 | `api_routes/system_settings.py`、`product_config.py` | `settings/SettingsWorkspace.tsx` | `settings-controller.cjs`、`secret-store.cjs` |
| 更新/端口 | Core 公共配置 | 状态展示 | `update-controller.cjs`、`service-supervisor.cjs`、`service-ports.cjs` |
