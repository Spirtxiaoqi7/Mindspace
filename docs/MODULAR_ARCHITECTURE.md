# Mindspace 模块化单体边界

> 文档状态：current。本文定义源码依赖和接口契约的当前机械门禁；目标是渐进收紧，不改变运行行为。

## 1. 总体原则

Mindspace 保持单仓库、模块化单体和单一桌面发布，不拆微服务。功能可以在自己的 UI、应用逻辑、领域规则和基础设施文件之间协同修改，但不得无理由穿透其他功能、存储实现或 Electron 宿主。

依赖方向为：

```text
entrypoint -> feature/application -> domain/ports
                                 <- adapters
```

`App.tsx`、`service.py` 和 `desktop/main.cjs` 在迁移期仍是组合中心，但禁止继续扩大职责。

## 2. 前端边界

当前 TypeScript 为 `7.0.x`，dependency-cruiser 尚未正式支持；现阶段由 `scripts/verify-frontend-boundaries.mjs` 执行零依赖静态检查。升级到兼容的 TypeScript `7.1+` 后再评估无行为变化替换。

当前规则：

| 规则 | 约束 |
|---|---|
| 功能隔离 | `chat`、`settings`、`characters`、`destiny`、`scenes` 不得直接互相导入 |
| 基础层向下 | `api.ts`、`chat-contract.ts`、`types.ts`、`ui/`、未来 `shared/` 不得反向依赖功能或应用入口 |
| 入口隔离 | 功能代码不得导入 `App.tsx`、`main.tsx` 或未来 `app/` |
| API 棘轮 | 只有脚本中登记的历史消费者可直接导入根级 `api.ts` |
| App 棘轮 | `App.tsx` 只允许依赖 `app/**`、`shared/**`、`features/*/index.ts`，不提供例外清单 |
| 动态导入 | 本地动态导入必须使用字面量，禁止通过运行时字符串绕过边界检查 |

`app/**`、`shared/**` 和 `features/*/index.ts` 是 App 唯一结构化入口。
`legacyAppDependencies` 已清空并删除，门禁不存在回填根级依赖的逃生口。
根 API 的新调用只能位于职责明确的 `rootApiGateways`；
`legacyRootApiConsumers` 仅记录尚待后续分域的历史消费者，任何新功能不得增加条目。

当前 `profile`、`memory`、`knowledge`、角色资料卡、诊断和语音舞台已经拥有真实功能实现，
不再由 `App.tsx` 保存其内部请求状态。聊天域已经建立 `useSessionDirectory`、
`useChatRuntime` 和 `useTurnComposer` 三个单一权威；语音域已经建立 `useTtsRuntime` 和
`useVoiceSessionRuntime`，分别管理播放与实时采集。应用数据、弹层、角色目录、会话场景、
模型选择、设置同步、ASR 就绪和会话维护也已进入对应 controller/hook。它们通过公开命令和 callback ref 协作，
不得共享可写内部 ref。共享值格式化位于 `shared/formatters`，禁止功能间复制。

完整前端职责、公共出口和迁移方法见 `docs/ARCHITECTURE_FRONTEND.md`。

## 3. Python 边界

`.importlinter` 当前保护：

| 规则 | 约束 |
|---|---|
| 包无环 | `mindspace_graph` 顶层兄弟模块不得形成依赖环 |
| Adapter 隔离 | `adapters` 不得导入 `api_routes` |
| API 隔离 | `api_routes` 不得新增对具体 `adapters` 的直接依赖 |
| Prompt 隔离 | `prompting`、`tool_chain` 不得依赖 API、Adapter、Server 或 Service |
| Application 隔离 | `application` 必须通过端口使用能力，不得导入 Adapter、API、Bootstrap、Server 或 Service |
| API 契约隔离 | `api_contracts` 可依赖内部模型，但不得依赖路由、Adapter 或进程入口 |
| Ports 隔离 | `ports` 不得依赖入口、路由、Adapter 或 Service |
| Adapter 独立 | 具体 Adapter 不得互相引用 |

原有三条跨层存储例外已经清零。JSON 原子写入能力现在通过
`mindspace_graph.infrastructure.storage` 的公开接口复用，API 路由和 Adapter 不再穿透
`adapters.file_storage` 的内部实现。后续禁止重新增加同类例外。

`JsonProfileRepository` 与 `JsonSessionRepository` 已分别位于独立物理模块；
`adapters.file_storage` 仅保留旧导入路径兼容，不得重新承载业务实现。组合根必须从物理模块导入。

模型实现选择也已经从 `application.conversation` 移到 `bootstrap`：应用服务只依赖
`LanguageModelFactoryPort`，设置刷新不得重新引入具体供应商类。

Prompt 采用 `prompting` 业务准备、`prompt_contributors` 分阶段贡献和
`prompt_templates` 纯文本模板、`prompt_blocks` 稳定编译的分层结构。贡献器不得读取
全局状态或反向导入 `prompting`；模板不得读取请求、Profile、ContextLedger 或隐式全局。

聊天 HTTP 边界使用 `ChatTurnCreateRequest`，内部图状态继续使用 `ChatRequest`。
服务端权威字段保留旧客户端输入兼容，但从公开 OpenAPI Schema 隐藏；SSE 继续维护独立事件协议，
不得套用普通 JSON 响应模型。

## 4. HTTP 契约

`scripts/export-api-contracts.py` 使用临时 runtime、demo LLM 和 browser ASR/TTS 创建隔离 FastAPI 应用，通过 `app.openapi()` 生成 `contracts/openapi/mindspace.openapi.json`。

生成规则：

| 项目 | 约束 |
|---|---|
| 数据隔离 | 禁止读取或写入 `A:\Mindspace\data` |
| 输出稳定 | UTF-8、排序 key、固定缩进、LF、无时间戳 |
| 检查模式 | `--check` 只在内存比较，不写文件 |
| 运行行为 | 不修改 URL、状态码、JSON、SSE、文件或桌面桥接行为 |

当前快照主要保护路由、请求体、参数、operation ID 和版本漂移。由于多数路由尚未声明结构化响应模型，它不是完整响应契约，不能直接用来替换全部前端 DTO。

## 5. 迁移顺序

1. 保持三个机械门禁持续通过，禁止新增例外。
2. 前端功能必须通过公开 `index.ts` 接入；保持 App 根级债务为零，并继续迁移根 API 历史消费者。
3. 将 Python 组合根从 `service.py` 分离，再建立 application/domain 目录。
4. 消除路由和 Adapter 对 `file_storage` 私有能力的横向依赖。
5. 为 Chat、Run、Session、Character 等普通 JSON 接口逐域增加公开请求和响应模型。
6. 生成 TypeScript 类型但先只做类型别名替换；保留当前错误语义和 `rawRequest()`。
7. 最后为 SSE 建立独立、带版本的判别联合；流、文件、音频和 Electron IPC 不套普通 JSON 契约。

## 6. CI 命令

```text
node scripts/verify-frontend-boundaries.mjs
uv run lint-imports
uv run python scripts/export-api-contracts.py --check
```

新增跨层例外、手工修改生成物或改变契约而未重生成，均应阻止合并。
