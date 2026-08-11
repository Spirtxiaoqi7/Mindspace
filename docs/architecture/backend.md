---
status: current
scope: Mindspace Python backend, API contracts, application services, and integration boundaries
last_reviewed: 2026-08-11
---

# Mindspace 后端架构

## 中文

### 入口与职责

FastAPI 入口是 `src/mindspace_graph/api.py`，它创建应用、建立 API 上下文、挂载静态目录并注册路由。`api_routes/**` 负责 HTTP/SSE 参数、状态码和错误映射；`api_contracts/**` 定义公开 HTTP DTO 并显式转换为内部模型；`models.py` 保存内部工作流模型；`bootstrap.py` 是唯一产品组合根；`service.py` 只保留兼容重导出。

`application/conversation.py` 管理 durable run、LangGraph 调用、SSE、取消、模型刷新和后台调度；`turn_preparation.py` 补全服务端权威请求；`retrieval_warmup.py` 协调检索预热；`ports.py` 定义能力协议和依赖集合；`graph.py`/`nodes.py` 执行工作流；`adapters/**` 实现模型、存储、检索和审计。

### 依赖边界

允许 `api_routes -> api_contracts -> models`，`api/api_routes -> bootstrap/application`，`bootstrap -> application/ports/adapters`，以及 `application -> ports/models/graph`。禁止 `models -> api_contracts`、`api_contracts -> api_routes`、`application -> api_routes`、`application -> 具体模型 adapter`、`adapters -> api_routes`。具体依赖在 `bootstrap.py` 组装并通过 `ProductContainer` 和 `Dependencies` 提供。

`ChatTurnCreateRequest` 是公开聊天请求，`ChatRequest` 是内部工作流请求。公开 DTO 不使客户端控制服务端权威字段。普通 JSON 路由可以声明 `response_model`；SSE、文件和音频路由应使用相应 Response，不能套普通 JSON 响应模型。

### 主要链路

1. `POST /api/v1/chat` 或流式入口到达 `api_routes/chat_runs.py`，验证 `ChatTurnCreateRequest`，调用 `to_internal()`。
2. `ConversationService` 以 `X-Request-ID` 创建或复用幂等的持久运行。
3. `TurnPreparationService` 补全角色、会话、活动、场景、引用、模型配置与检索状态。
4. LangGraph 执行并返回 `ChatResponse`；普通请求返回 JSON，流式请求以 `text/event-stream` 产生可恢复的序列事件。
5. run、回复、attempt 和终态被持久化；恢复使用 run、`after` 或 `Last-Event-ID`，不得改写事件内容。

### 修改导航

1. 新用例放入 `application/**`，构造函数显式接收 settings、协议或查询函数；若属对话运行生命周期，由 `ConversationService` 调度；若是产品级共享能力，由 `bootstrap.py` 创建。
2. 新外部能力先在 `ports.py` 定义最小协议，再在 `adapters/**` 实现并由组合根注入。
3. 新模型实现 `LanguageModelPort`，只在 `_ConfiguredLanguageModelFactory` 选择；应用服务和图节点继续通过 `LanguageModelFactoryPort` 使用它。
4. 新 HTTP 输入先定义公开 DTO 和内部转换，再在 `api_routes/**` 调用应用服务；保留既有 URL、状态码和流式语义。

### 禁止事项

- 禁止在应用服务、路由或图节点中实例化或选择具体供应商模型。
- 禁止路由创建 adapter 或访问 adapter 私有函数；禁止 adapter 依赖路由。
- 禁止在 `service.py` 增加业务逻辑，或把 `ProductContainer`/`build_container()` 放回该模块。
- 禁止把检索预热任务表放回 `ConversationService`，或在 `TurnPreparationService` 中启动后台预热。
- 禁止为 SSE 声明普通 JSON 响应模型，或让客户端提交的敏感/上下文字段成为权威值。

## English

### Entry points and responsibilities

The FastAPI entry point is `src/mindspace_graph/api.py`; it creates the application, establishes API context, mounts static assets, and registers routes. `api_routes/**` owns HTTP/SSE parameters, status codes, and error mapping; `api_contracts/**` defines public HTTP DTOs and explicitly converts them to internal models; `models.py` holds internal workflow models; `bootstrap.py` is the sole product composition root; `service.py` retains compatibility re-exports only.

`application/conversation.py` owns durable runs, LangGraph invocation, SSE, cancellation, model refresh, and background scheduling; `turn_preparation.py` completes server-authoritative requests; `retrieval_warmup.py` coordinates retrieval warmup; `ports.py` defines capability protocols and dependency collections; `graph.py`/`nodes.py` execute the workflow; `adapters/**` implements model, storage, retrieval, and audit capabilities.

### Dependency boundaries

Allowed dependencies are `api_routes -> api_contracts -> models`, `api/api_routes -> bootstrap/application`, `bootstrap -> application/ports/adapters`, and `application -> ports/models/graph`. Forbidden dependencies are `models -> api_contracts`, `api_contracts -> api_routes`, `application -> api_routes`, `application -> concrete model adapters`, and `adapters -> api_routes`. Concrete dependencies are composed in `bootstrap.py` and supplied through `ProductContainer` and `Dependencies`.

`ChatTurnCreateRequest` is the public chat request and `ChatRequest` is the internal workflow request. Public DTOs do not let clients control server-authoritative fields. Normal JSON routes may declare `response_model`; SSE, file, and audio routes must use the appropriate Response and must not be forced into a normal JSON response model.

### Primary flow

1. `POST /api/v1/chat` or a streaming entry reaches `api_routes/chat_runs.py`, validates `ChatTurnCreateRequest`, and calls `to_internal()`.
2. `ConversationService` creates or reuses an idempotent durable run using `X-Request-ID`.
3. `TurnPreparationService` completes character, session, activity, scene, reference, model configuration, and retrieval state.
4. LangGraph executes and returns `ChatResponse`; normal requests return JSON, while streaming requests produce resumable sequence events as `text/event-stream`.
5. Runs, replies, attempts, and terminal state are persisted; resume uses the run, `after`, or `Last-Event-ID` and must not rewrite event content.

### Change navigation

1. Place a new use case in `application/**` with an explicit constructor for settings, protocols, or query functions; let `ConversationService` schedule work that belongs to the conversation-run lifecycle, and let `bootstrap.py` create product-wide shared capability.
2. Define the smallest protocol in `ports.py` before implementing a new external capability in `adapters/**`, then inject it from the composition root.
3. Implement `LanguageModelPort` for a new model and select it only in `_ConfiguredLanguageModelFactory`; application services and graph nodes continue to use it through `LanguageModelFactoryPort`.
4. Define a public DTO and internal conversion before adding new HTTP input, then call the application service from `api_routes/**`; preserve existing URLs, status codes, and streaming semantics.

### Prohibitions

- Do not instantiate or select a concrete provider model in application services, routes, or graph nodes.
- Do not create adapters or access adapter-private functions from routes; adapters must not depend on routes.
- Do not add business logic to `service.py` or move `ProductContainer`/`build_container()` back into that module.
- Do not move retrieval-warmup task tables back into `ConversationService` or start background warmup in `TurnPreparationService`.
- Do not declare a normal JSON response model for SSE or let client-supplied sensitive/context fields become authoritative.
