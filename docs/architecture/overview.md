---
status: current
scope: Mindspace product architecture, cross-layer boundaries, and maintenance navigation
last_reviewed: 2026-08-11
---

# Mindspace 架构总览

## 中文

### 定位与入口

Mindspace 是单仓库、单一桌面发布的模块化单体；可编辑开发源为 `A:\RAG\Mindspace-admin`。产品由 Desktop Launcher 启动 Core，页面通过 preload 的窄 IPC 和 Core HTTP/SSE API 工作。后端 FastAPI 入口是 `src/mindspace_graph/api.py`，前端挂载入口是 `frontend/src/main.tsx`，桌面组合入口是 `desktop/main.cjs`。

### 职责与依赖边界

- **Desktop**：管理窗口、Core 生命周期、更新、端口和受控设置桥；不承载产品业务状态，也不把秘密暴露给页面。
- **Frontend**：`app/**` 负责应用壳、Provider、路由和组合状态；`features/**` 负责产品能力；`shared/**` 仅放无业务归属的共享能力。功能只通过各自 `index.ts` 提供最小公共接口。
- **Backend**：`api.py` 和 `api_routes/**` 处理 HTTP/SSE；`api_contracts/**` 定义公开 DTO；`application/**` 编排用例；`ports.py` 定义能力协议；`graph.py`/`nodes.py` 执行 LangGraph；`adapters/**` 实现模型、存储、检索和审计；`bootstrap.py` 是唯一产品组合根。
- 允许方向为 `入口 -> 组合/路由 -> feature 或 application -> models/ports/graph`，具体 adapter 由组合根注入。禁止功能反向依赖应用壳，禁止应用服务、路由或图节点直接选择具体模型或穿透 adapter 私有实现。

### 主要链路

1. Launcher 从受控设置读取 provider 和端口配置，启动并监督 Core。
2. 页面经 preload 和 API 提交请求；聊天使用普通 JSON 或 SSE。
3. 聊天路由验证公开 DTO，转换为内部请求，交给 `ConversationService` 创建或恢复 durable run。
4. `TurnPreparationService` 补全服务端权威的角色、会话、场景、引用和模型上下文；LangGraph 生成回复或一次原生工具尝试。
5. 回复、attempt、run 终态和 SSE 序列按会话/回合持久化；刷新或重连按 run/turn 恢复，不跨会话伪造结果。

### 修改导航

- 前端结构、权威状态和公共出口：`architecture/frontend.md`。
- 后端入口、服务、协议和扩展规则：`architecture/backend.md`。
- 先从 API 路由、SSE 事件名或状态字段用 `rg` 追到消费者，再改最小职责边界。新增跨层能力时，先定义窄接口，再由功能出口或 `bootstrap.py` 组装。

### 禁止事项

- 不得把 `A:\Mindspace` 运行时目录当作开发源。
- 不得把 API 密钥、系统提示、活动或场景等客户端输入当作服务端权威值。
- 不得用 SSE 伪装成普通 JSON 契约，或改变既有恢复游标、事件顺序和媒体语义。
- 不得通过根级聚合、动态路径或跨功能内部导入绕过边界。
- 不得把工具失败描述为已验证或已完成；工具结果只是本轮数据。

## English

### Positioning and entry points

Mindspace is a modular monolith in one repository and one desktop release. The editable development source is `A:\RAG\Mindspace-admin`. The Desktop Launcher starts Core, while the page uses narrow preload IPC plus Core HTTP/SSE APIs. The backend FastAPI entry point is `src/mindspace_graph/api.py`, the frontend mount entry point is `frontend/src/main.tsx`, and the desktop composition entry point is `desktop/main.cjs`.

### Responsibilities and dependency boundaries

- **Desktop**: owns windows, Core lifecycle, updates, ports, and the controlled settings bridge; it does not own product state or expose secrets to the page.
- **Frontend**: `app/**` owns the application shell, providers, routing, and composition state; `features/**` owns product capabilities; `shared/**` contains only business-neutral shared capabilities. Features expose only minimal public interfaces through their `index.ts` files.
- **Backend**: `api.py` and `api_routes/**` handle HTTP/SSE; `api_contracts/**` defines public DTOs; `application/**` orchestrates use cases; `ports.py` defines capability protocols; `graph.py`/`nodes.py` execute LangGraph; `adapters/**` implements model, storage, retrieval, and audit capabilities; `bootstrap.py` is the sole product composition root.
- The allowed direction is `entry point -> composition/route -> feature or application -> models/ports/graph`; concrete adapters are injected by the composition root. Features must not depend back on the application shell, and application services, routes, and graph nodes must not select concrete models or reach through adapter-private implementations.

### Primary flow

1. The Launcher reads provider and port configuration through controlled settings, then starts and supervises Core.
2. The page submits requests through preload and APIs; chat uses normal JSON or SSE.
3. The chat route validates a public DTO, converts it to an internal request, and asks `ConversationService` to create or resume a durable run.
4. `TurnPreparationService` completes server-authoritative character, session, scene, reference, and model context; LangGraph produces a reply or one native tool attempt.
5. Replies, attempts, run completion, and SSE sequence data are persisted by session/turn; reload and reconnect resume by run/turn without crossing sessions or fabricating results.

### Change navigation

- For frontend structure, authoritative state, and public exports, see `architecture/frontend.md`.
- For backend entry points, services, protocols, and extension rules, see `architecture/backend.md`.
- Start from an API route, SSE event name, or state field and use `rg` to trace consumers before changing the smallest responsible boundary. For a new cross-layer capability, define a narrow interface first, then compose it through a feature export or `bootstrap.py`.

### Prohibitions

- Do not treat the `A:\Mindspace` runtime directory as the development source.
- Do not treat client-supplied API keys, system prompts, activity, or scene values as server-authoritative.
- Do not disguise SSE as a normal JSON contract or change established resume cursors, event order, or media semantics.
- Do not bypass boundaries through root-level aggregators, dynamic paths, or imports into another feature's internals.
- Do not present a failed tool as verified or complete; tool output is data for the current turn only.
