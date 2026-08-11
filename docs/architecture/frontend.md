---
status: current
scope: Mindspace frontend composition, feature boundaries, and maintenance rules
last_reviewed: 2026-08-11
---

# Mindspace 前端架构

## 中文

### 入口与职责

`frontend/src/main.tsx` 负责挂载。`frontend/src/app/**` 承担应用壳、Provider、路由和组合层视图状态；`App.tsx` 是迁移期组合根；`features/**` 承担业务能力；`shared/**` 提供无业务归属的纯共享能力。`App.tsx` 只组合 Chat、Character、Destiny、Scene、Settings、Profile、Memory、Knowledge 和 Voice，不重新实现功能内部状态、请求或运行时。

### 权威状态与公共接口

- `useSessionDirectory`：会话目录、选择、恢复和持久化；`useChatRuntime`：消息、回合、active run、SSE 与召回；`useTurnComposer`：草稿、附件、互动和请求构造。
- `useTtsRuntime`：播放队列与 delivery evidence；`useVoiceSessionRuntime`：麦克风、ASR、VAD、重连与陪伴计时。
- 应用数据、弹层、角色目录、会话场景、会话维护、模型选择和 ASR 就绪状态分别由对应 controller/hook 独占。
- 每个功能从 `frontend/src/features/<feature>/index.ts` 暴露必要组件、Hook 和类型。公共出口是稳定依赖面，不得为方便而重导出全部内部实现。

### 依赖边界与主要链路

`App.tsx` 仅可导入 `app/**`、`shared/**` 和 `features/*/index.ts`。功能不得导入 `App.tsx`、`main.tsx` 或应用壳，也不得穿透另一功能的内部文件。根级 `api.ts`、`chat-contract.ts`、`types.ts`、`ui/**` 和 `shared/**` 不得反向依赖入口或功能实现；本地动态导入必须使用字符串字面量。

聊天链路为 UI/Composer 构造回合请求，Chat Runtime 提交并解释 SSE 业务事件，再将结果交给消息视图、TTS 和 Voice Runtime。跨运行时协作只能使用公开命令、只读查询或 callback ref，不能共享可写内部 ref。

### 修改导航

1. 在现有功能内定位完整状态、命令、错误和持久化边界。
2. 新能力放入所属 `features/<feature>/**`；应用壳、Provider、路由和组合状态放入 `app/**`；纯通用能力放入 `shared/**`。
3. 若组合层需要能力，从功能 `index.ts` 增加最小公开 API，并让一个 hook/controller 成为唯一状态权威。
4. 保持 DOM、文案、URL、请求 payload、SSE 顺序和 localStorage key 的行为兼容；边界门禁由 `scripts/verify-frontend-boundaries.mjs` 保护。

### 禁止事项

- 禁止在 App、组件和 Hook 中保存同一业务状态的镜像副本。
- 禁止让 App 直接导入根级 API、契约、类型或 UI 私有模块，或新增根级聚合文件绕过检查。
- 禁止功能间直接导入内部 reducer、私有请求函数或可写 ref。
- 禁止为了拆分而只搬 JSX、只做 re-export，或留下旧实现并形成第二套状态。

## English

### Entry points and responsibilities

`frontend/src/main.tsx` mounts the application. `frontend/src/app/**` owns the application shell, providers, routing, and composition-layer view state; `App.tsx` is the transitional composition root; `features/**` owns business capabilities; `shared/**` provides business-neutral pure shared capabilities. `App.tsx` only composes Chat, Character, Destiny, Scene, Settings, Profile, Memory, Knowledge, and Voice; it does not reimplement feature-internal state, requests, or runtimes.

### Authoritative state and public interfaces

- `useSessionDirectory` owns the session directory, selection, recovery, and persistence; `useChatRuntime` owns messages, turns, the active run, SSE, and retrieval; `useTurnComposer` owns drafts, attachments, interactions, and request construction.
- `useTtsRuntime` owns the playback queue and delivery evidence; `useVoiceSessionRuntime` owns microphone, ASR, VAD, reconnect, and companion timing.
- Application data, modals, character directory, conversation scene, conversation maintenance, model selection, and ASR readiness are each exclusively owned by their corresponding controller/hook.
- Each feature exports needed components, hooks, and types from `frontend/src/features/<feature>/index.ts`. A public export is a stable dependency surface, not a convenience re-export of all internals.

### Dependency boundaries and primary flow

`App.tsx` may import only `app/**`, `shared/**`, and `features/*/index.ts`. Features may not import `App.tsx`, `main.tsx`, or the application shell, and may not reach into another feature's internals. Root `api.ts`, `chat-contract.ts`, `types.ts`, `ui/**`, and `shared/**` must not depend back on entry points or feature implementations; local dynamic imports must use string literals.

The chat flow constructs a turn request in UI/Composer, submits and interprets SSE business events in Chat Runtime, then passes results to message views, TTS, and Voice Runtime. Cross-runtime cooperation may use only public commands, read-only queries, or callback refs, never shared writable internal refs.

### Change navigation

1. Locate the complete state, command, error, and persistence boundary in the existing feature.
2. Place a new capability in its owning `features/<feature>/**`; place application shell, providers, routing, and composition state in `app/**`; place pure general-purpose capability in `shared/**`.
3. When the composition layer needs a capability, add the smallest public API to the feature `index.ts` and make one hook/controller the sole state authority.
4. Preserve behavior for DOM, copy, URLs, request payloads, SSE ordering, and localStorage keys; `scripts/verify-frontend-boundaries.mjs` protects the boundary gate.

### Prohibitions

- Do not keep mirrored copies of the same business state in App, components, and hooks.
- Do not let App import root API, contracts, types, or private UI modules directly, and do not create a new root-level aggregator to bypass checks.
- Do not import another feature's internal reducers, private request functions, or writable refs.
- Do not split code by merely moving JSX or adding re-exports, or leave the old implementation behind as a second state system.
