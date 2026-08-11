# Mindspace 前端架构

> 文档状态：current。本文描述当前前端组合边界、功能公共出口和状态权威；它不是未来重写提案。

## 1. 组合结构

前端采用模块化单体。`main.tsx` 负责挂载，`app/**` 承载应用壳、Provider、路由和视图状态，`App.tsx` 是迁移期组合根，`features/**` 承载业务能力，`shared/**` 承载无业务归属的纯共享能力。

```text
main.tsx
  -> app/**
  -> App.tsx
       -> features/*/index.ts
       -> shared/**
```

`App.tsx` 可以组合多个功能，但不得重新实现功能内部状态、请求流程或运行时。

## 2. App 当前职责

`App.tsx` 当前保留以下跨域职责：

- 组合 Chat、Character、Destiny、Scene、Settings、Profile、Memory、Knowledge 和 Voice 功能。
- 协调全局设置、当前角色和跨功能导航。
- 解释聊天 SSE 业务事件，并把结果交给 Chat、TTS 和 Voice Runtime。
- 组装 `ChatWorkspace` 所需的语义 ViewModel 与 Commands。
- 管理全局弹层选择、检查器和通知等组合层 UI 状态。
- 组合 app controller 与各 feature 的公开命令，不直接访问根级基础模块。

`App.tsx` 不应再持有以下权威状态：

- 会话目录和当前会话恢复规则。
- 消息运行、回合、SSE 重连和 active run。
- 输入草稿、附件、互动标签、回复目标和重生成草稿。
- TTS 队列、播放器、音频节点、Qwen 缓冲和 delivery evidence。
- 麦克风、ASR WebSocket、VAD、语音重连和连续陪伴计时。
- Profile、Memory 或 Knowledge 弹层内部请求和表单状态。

## 3. 功能公共出口

每个功能通过 `frontend/src/features/<feature>/index.ts` 暴露公共组件、Hook 和必要类型。组合层不得直接导入功能内部文件。

当前公共功能域包括：

| 功能 | 公共职责 |
|---|---|
| `chat` | 工作区、执行检查器、会话目录、消息 Runtime、回合输入编排和会话维护 |
| `characters` | 角色库、角色选择、资料卡、角色目录和头像能力 |
| `destiny` | 命格创建工作区 |
| `knowledge` | 知识库管理弹层 |
| `memory` | 长期记忆与事件记忆弹层 |
| `profile` | 用户与角色资料编辑 |
| `scenes` | 场景选择、当前会话场景状态与场景资源入口 |
| `settings` | 设置、诊断、模型切换、设置同步和通用设置弹层组件 |
| `voice` | TTS Runtime、实时语音 Runtime、语音舞台和语音类型 |

公共 `index.ts` 是稳定依赖面，不是把整个目录重新导出的便利文件。新增导出前应确认调用者确实需要该能力，并避免暴露内部 reducer、私有请求函数和可写 ref。

## 4. Hook 权威状态

| Hook | 唯一权威 |
|---|---|
| `useSessionDirectory` | 会话列表、搜索、选择、恢复、新建、删除和 session ID 持久化 |
| `useChatRuntime` | `messages`、`round`、生成状态、active run、SSE sequence、检查器事件和召回结果 |
| `useTurnComposer` | draft、附件、互动多选、回复目标、重生成草稿和 `ChatTurnRequest` 构造 |
| `useTtsRuntime` | TTS provider 执行、播放队列、当前 segment、音频上下文、Qwen 缓冲和 delivery evidence |
| `useVoiceSessionRuntime` | `VoiceSessionState`、麦克风、采集图、ASR WebSocket、VAD/evidence、重连和连续陪伴计时 |
| `useApplicationData` | 首次设置、头像与目录并行加载，以及初始化完成状态 |
| `useModalCoordinator` | 全局弹层、脏状态关闭确认和资料卡入口状态 |
| `useCharacterDirectory` | 角色摘要列表与刷新请求 |
| `useConversationScene` | 当前会话场景与刷新请求 |
| `useConversationMaintenance` | 删除回复、清空上下文及其确认和持久化清理 |
| `useModelSelection` | 可用模型列表、加载状态和模型切换 |
| `useAsrReadiness` | ASR 就绪状态和定时轮询 |

同一状态不得在 App、组件和 Hook 中各保留一份。跨 Runtime 协作必须使用公开命令、只读查询或 callback ref，禁止直接共享可写内部 ref。

## 5. 依赖规则

`scripts/verify-frontend-boundaries.mjs` 对本地静态依赖执行机械检查。

### App 允许入口

- `src/app/**`
- `src/shared/**`
- `src/features/*/index.ts`

这些是结构化入口，由规则识别，不属于 legacy 清单。

### App 根级债务

`legacyAppDependencies` 已清空并从门禁实现删除。`App.tsx` 不再直接导入
`api.ts`、`chat-contract.ts`、`types.ts`、`ui/avatar.tsx` 或
`ui/styledConfirm.ts`，也不存在可登记新例外的清单。

### 其他规则

- 功能不得导入 `App.tsx`、`main.tsx` 或应用壳。
- 功能间协作优先通过公共出口和窄类型契约完成，禁止穿透另一功能的内部文件。
- `api.ts`、`chat-contract.ts`、`types.ts`、`ui/**` 和 `shared/**` 不得反向依赖应用入口或功能实现。
- 本地动态导入必须使用字符串字面量。
- 新增根级 API 消费者必须进入命名明确的 app/feature 网关并登记到
  `rootApiGateways`；不得扩充 `legacyRootApiConsumers`。

## 6. 迁移指南

1. 在原位置确认完整状态、命令、错误和持久化边界。
2. 选择一个高内聚职责，建立功能组件、Controller 或 Runtime。
3. 让新边界成为唯一状态权威，禁止保留 App 镜像状态。
4. 使用分组依赖、公开命令和 callback ref 处理跨域协作。
5. 从功能 `index.ts` 暴露最小公共 API。
6. 将 App 改为组合接线，不改变 DOM、文案、URL、请求 payload、SSE 顺序或 localStorage key。
7. 删除旧实现、旧 ref、旧 helper 和对应 legacy 清单项。
8. 更新本文和 `MODULAR_ARCHITECTURE.md`，说明新的权威边界与剩余债务。

迁移不以 App 行数为唯一目标。优先保证单一权威、可读依赖和行为等价，避免只搬 JSX、只做 re-export 或创建第二套状态。

## 7. 新代码落点

| 代码类型 | 位置 |
|---|---|
| 应用壳、Provider、路由、视图状态 | `frontend/src/app/**` |
| 功能组件、Hook、Controller、Runtime | `frontend/src/features/<feature>/**` |
| 功能公共 API | `frontend/src/features/<feature>/index.ts` |
| 无业务归属的纯函数和通用类型 | `frontend/src/shared/**` |
| 兼容根模块 | 仅供领域网关内部逐步迁移，App 不得直接引用 |

禁止为了绕过边界检查创建新的根级聚合文件。
