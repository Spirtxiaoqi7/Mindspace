> 文档状态：report。仅记录当次审查结论，不得替代当前 runbook；当前权威见 `docs/INDEX.md`。

# Mindspace 0.8.3 代码大审查：第一阶段

状态：`current / review-only`
日期：`2026-08-10`
审查源：`A:\RAG\Mindspace-admin`
桌面运行目录：`A:\Mindspace`，不作为开发源
用户数据：`A:\Mindspace\data`，本阶段未修改

## 1. 审查目标

0.8.3 的第一阶段不是功能开发，而是为后续稳定上线、多人分支协作和核心代码保护建立可证明的代码边界。

1. 防止旧逻辑、重复实现、错误端口、错误路径、死代码和补丁叠补丁继续进入主链。
2. 让其他开发者能够理解模块职责，在小而独立的分支中提交修改，不必同时改动半个系统。
3. 找出静态可确认的隐藏缺陷，并为运行验证建立明确用例。
4. 在核心代码保护之前，先清理 source map、明文凭据、内部测试脚本、用户数据副产物和升级残留。
5. 区分产品既定行为、兼容接口、历史文档和真正技术债，避免机械删代码。

## 2. 方法与限制

本阶段调用五个 `gpt-5.6-terra / high` 只读审查代理，由主审统一裁决。

| 路线 | 主责 | 允许跨查 |
|---|---|---|
| A | 前端交互、状态恢复、API 契约、Electron 前端桥接 | 对应 FastAPI、SSE、持久化 |
| B | FastAPI、ConversationService、LangGraph、Prompt、原生工具 | 前端工具卡、Provider 适配器 |
| C | 数据、角色、命格、V2、RAG、记忆、任务、迁移 | API、图持久化节点 |
| D | 桌面运行时、端口、音频、启动、更新、打包、发布 | Core 配置、静态资源 |
| E | 测试、文档、依赖、安全、报告和全库漏检 | 所有一级目录的覆盖对账 |

限制：未运行测试、未调用真实 API、未执行 Git 操作、未修改业务代码。所有结论按以下状态标注。

| 状态 | 含义 |
|---|---|
| `CONFIRMED` | 静态调用链足以确认缺陷或确定性风险 |
| `RUNTIME-VERIFY` | 静态证据充分，但仍需隔离运行用例确认影响 |
| `PRODUCT` | 已明确的产品行为，不得按缺陷删除 |
| `CANDIDATE` | 疑似废弃或重复，删除前必须证明无调用方 |

## 3. 覆盖范围

排除 `.git`、`node_modules`、虚拟环境、缓存、二进制、模型、图片、安装包、构建目录、`vendor` 和生成后的 Web bundle 后，共清点 `521` 个可维护或需治理的文件。

| 一级区域 | 文件数 | 审查责任 |
|---|---:|---|
| `frontend` | 189 | A、E |
| `scripts` | 65 | D、E |
| `desktop` | 61 | A、D、E |
| `docs` | 60 | B、C、D、E |
| `src` | 59 | B、C、D |
| `tests` | 40 | C、E |
| `reports` | 35 | E |
| 根文件 | 8 | B、C、D、E |
| `config` | 2 | D、E |
| `assets` | 1 个可维护文本入口 | D、E；大体积二进制未审计 |
| `deploy` | 1 | D、E |

明确排除项仍需治理其边界，而不是视为不存在：`.pytest-*`、`.runtime-*`、`.real-api-*`、`.venv*`、`dist*`、`runtime`、`artifacts`、`backups`、`desktop/bootstrap/runtime-bundle`。

## 4. 主审裁决

### 4.1 已排除的误报或产品行为

1. `PRODUCT`：打开消息“更多”时自动建立 `@本条消息` 引用，是 2026-08-10 明确确认的交互，不是缺陷。需要补测试锁定行为，而不是删除 `onToggle -> onReply`。
2. 当前默认端口 `8765`、ASR `8766`、GPT-SoVITS `5055`、Qwen `8091` 在主要配置中一致，未发现已发生的错误端口调用。
3. `shared_chapters`、`emotion_disabled.py`、`capabilities.py`、`tool_chain.py` 和 `native_tools.py` 仍有生产引用，不能按文件名直接删除。
4. 多个返回 `410` 的旧接口目前属于兼容哨兵。前端零调用、迁移窗口结束并写明移除版本后，才可删除或集中到 compatibility router。
5. 未发现仓库文件中存在字面真实 API Key 或私钥。`reports` 的风险是敏感内容和误提交，不等于已确认密钥泄露。

### 4.2 总体结论

未发现静态可确认的 `P0`。存在多项 `P1`，所以当前代码不应直接进入核心代码保护阶段。最优先的不是混淆或加密，而是数据一致性、请求幂等、发布边界和单一权威来源。

## 5. P1：上线或加密前必须处理

### P1-01 重生成不是跨层原子操作

状态：`CONFIRMED`

前端重新生成只复用纯文本，未复用原轮的 `interactions`、`attachments` 和 `reply_to_message_id`。后端只替换会话消息，同轮旧 Context Ledger、压缩输入和已提取结构化记忆没有同步失效。

证据：

- `frontend/src/App.tsx:3456`
- `src/mindspace_graph/adapters/file_storage.py:790`
- `src/mindspace_graph/nodes.py:985`
- `src/mindspace_graph/nodes.py:1017`
- `src/mindspace_graph/adapters/structured_memory.py:559`

影响：用户看到的是新回复，但旧回复仍可能从摘要、RAG 或结构化记忆重新出现；纯互动、附件和引用的重生成会改变语义。

整改目标：建立 `replace_turn` 事务，一次处理会话消息、Context Ledger、压缩任务、结构化记忆、工具回执和删除事件。前端重生成以原始结构化用户消息为输入。

验收：同轮先生成并触发记忆写回，再重新生成；所有可召回层只能保留新回复证据。

### P1-02 结构化记忆容量跨角色互相淘汰

状态：`CONFIRMED`

`_enforce_active_limits()` 仅按 `field_code` 分组，没有加入 `character_id`。

证据：`src/mindspace_graph/adapters/structured_memory.py:304-313`

影响：角色 B 写满偏好上限时可能淘汰角色 A 的活动记忆，违反角色隔离。

整改目标：容量键使用 `(owner_type, character_id, field_code)`；全局用户字段使用独立 owner。

### P1-03 指定角色重建记忆实际重置全部角色

状态：`CONFIRMED`

`memory_service.rebuild(character_id=...)` 仅验证角色存在，随后仍执行全局 `store.reset()` 并重建所有 owner。

证据：`src/mindspace_graph/memory_service.py:61-112`

影响：编辑或提交一个角色可能重写其他角色的活动记忆、墓碑、未标注池和证据 ID。

整改目标：定向 rebuild 只能作用于指定 owner；全量 rebuild 使用独立管理员命令和显式确认。

### P1-04 命格 GET 会产生破坏性写入

状态：`CONFIRMED`

`DestinyService.get()` 发现命签不符合当前校验时，会保存失败状态并清空 `cards_by_slot`、`selections` 和 `final_card`。

证据：

- `src/mindspace_graph/destiny.py:767-793`
- `src/mindspace_graph/api.py:718`

影响：读取旅程即可丢失 96 签、12 项选择和最终卡；刷新页面也可能成为破坏动作。

整改目标：GET 必须纯读，只返回 `requires_regeneration` 和诊断；清理使用带 revision 的显式命令。

### P1-05 同步聊天的幂等检查晚于模型和工具执行

状态：`RUNTIME-VERIFY`

同步 `/chat` 先执行 `graph.ainvoke()`，到持久化阶段才识别已提交回合。流式链路则先创建 durable run，两条入口不是同一状态机。

证据：

- `src/mindspace_graph/api.py:1382`
- `src/mindspace_graph/service.py:421-435`
- `src/mindspace_graph/nodes.py:931`

影响：重复或并发请求可能重复消耗模型调用、重复联网；任务工具虽有独立幂等，整轮仍不统一。

整改目标：进入图前以 `session_id + request_id + idempotency_digest` 建立、复用或拒绝 durable turn。

### P1-06 模型调用预算不等于真实 Provider 请求数

状态：`CONFIRMED`

适配器存在兼容参数重试、空输出重试和首次连接重试，但图状态只统计逻辑模型节点。

证据：

- `src/mindspace_graph/adapters/openai_compatible.py:329-450`
- `src/mindspace_graph/nodes.py:136`

影响：用户看到“一次调用”，实际可能发生多次 HTTP 请求；成本、失败率和审计报告不准确。

整改目标：分开记录 `logical_model_calls` 与 `provider_http_attempts`，每次重试写入原因、耗时和请求 ID。

### P1-07 Prompt 存在先构建再覆盖的双路径

状态：`CONFIRMED`

`build_system_prompt()` 前段组装身份、性别和完整角色规则，后段重新赋值为 `compact_system_prompt()`，导致前段部分规则不进入最终 Prompt。

证据：`src/mindspace_graph/prompting.py:488-550`

影响：维护者看到代码存在便误以为模型收到；性别、生理一致性和角色约束可能实际缺失。

整改目标：保留唯一 Prompt builder；每个最终层具有稳定 ID，可由执行详情展示和快照测试证明。

### P1-08 Core 引导升级会残留旧文件

状态：`CONFIRMED`

`desktop/bootstrap-core.cjs` 使用 `fs.cpSync(... force: true)` 覆盖复制，但不删除新包中已不存在的旧文件。

证据：`desktop/bootstrap-core.cjs:69-103`

影响：旧路由、旧页面和旧脚本可继续存在，是“桌面仍加载旧版本”类问题的直接结构性原因；代码保护后还可能残留未保护旧源码。

整改目标：staging + manifest allowlist + 原子切换；用户数据、模型和环境独立保留。

### P1-09 更新与迁移按端口强杀进程

状态：`CONFIRMED`

`scripts/stop-services.ps1` 对监听 `8765/8766/5055` 的 PID 直接 `taskkill /T /F`，未验证进程归属。

证据：`scripts/stop-services.ps1:7-14`

影响：可能杀掉其他项目、其他 Mindspace 实例或用户手动服务。

整改目标：服务启动时写 PID、可执行路径和 nonce；停止仅作用于受管进程树。端口扫描只能诊断。

### P1-10 完整性检查默认旧路径且失败时返回成功

状态：`CONFIRMED`

`verify-source-integrity.ps1` 默认指向 `A:\Mindscape`，路径缺失时输出 skipped 并以 `0` 退出。

证据：`scripts/verify-source-integrity.ps1:5-15`

影响：维护和发布可能获得错误绿灯。

整改目标：删除历史检查或强制显式目标；缺失、错误、篡改均返回非零。

### P1-11 Core 发布包可能携带内部真实 API 与桌面数据脚本

状态：`CONFIRMED`

`build-update.ps1` 将整个 `scripts` 目录复制到 Core，而排除列表不是运行脚本正向 allowlist。

证据：

- `scripts/build-update.ps1:38-109`
- `scripts/run_deepseek_desktop_r18_90.py`
- `scripts/run_gemma_sample_inputs.py`
- `scripts/run_082_real_api_regression.py`
- `scripts/run_roleplay_state_machine_benchmark.py`
- `scripts/today_81_acceptance.py`

影响：开发者验收脚本、真实桌面路径和用户数据读取逻辑进入生产包，扩大隐私和攻击面。

整改目标：发布包只允许运行所需脚本；基准、迁移、真实 API 和内部验收移动到 `tools/internal` 且永不打包。

### P1-12 凭据以明文写入配置，且 Launcher/Core 双写

状态：`CONFIRMED`

`ProductConfigStore` 将 LLM、ASR 和云 TTS API Key 写入 JSON；Launcher 和 Core 都能写设置。

证据：

- `src/mindspace_graph/product_config.py:105-116`
- `src/mindspace_graph/product_config.py:185-201`
- `desktop/main.cjs:645-727`
- `src/mindspace_graph/api.py:523`

影响：API 返回脱敏不能保护磁盘；事务失败时可能出现 Launcher 文件与 Core 配置两个真相。

整改目标：凭据迁移至 Windows DPAPI/Credential Manager；Core 成为设置唯一写入者，Launcher 只发请求。

### P1-13 生产公开 source map

状态：`CONFIRMED`

Vite 构建开启 `sourcemap: true`，输出到服务端静态目录；生成的 map 含 `sourcesContent`，服务端公开挂载。

证据：

- `frontend/vite.config.ts:9-12`
- `src/mindspace_graph/api.py:393-464`

影响：前端源码可被直接还原，后续混淆或代码保护失效。

整改目标：生产构建禁用 source map；调试 map 输出到不打包、不托管的内部目录。

### P1-14 真实报告与临时目录缺少提交隔离

状态：`CONFIRMED`

`reports` 含真实模型回复和私有/R18 回归内容；大量 `.real-api-*`、`.runtime-*`、`.deploy-profile-*`、`.tmp*` 未被忽略。

证据：

- `reports/`
- `.gitignore:36-37`

影响：污染检索、打包、分支 diff 和敏感内容扫描，并存在误提交风险。未执行 Git，因此本阶段不声称它们已经被追踪。

整改目标：真实报告移出仓库或只保留脱敏汇总；统一工作目录规范和忽略规则。

### P1-15 0.8.2 新交互链无自动化门禁

状态：`CONFIRMED`

测试中未覆盖模型列表、互动单选/多选、附件、引用、空工具结果、流式恢复和新输入框；`.github` 没有 CI workflow。

影响：近期已出现空工具对象、互动不生效和恢复状态错误，这类回归只能靠用户手测发现。

整改目标：建立离线契约测试为强制门禁，真实 API 验收保持隔离且只在明确阶段执行。

### P1-16 桌面产品窗口外链策略不一致

状态：`CONFIRMED`

产品窗口的 `setWindowOpenHandler` 将任意 URL 交给 `shell.openExternal()`，而启动器 IPC 已有 HTTPS 与主机白名单策略。

证据：`desktop/main.cjs:2122-2241`

整改目标：所有外链共用同一 URL policy，拒绝 `file:`、自定义协议和非白名单目标。

## 6. P2：0.8.3 应处理

| 编号 | 问题 | 状态 | 主要证据 |
|---|---|---|---|
| P2-01 | 无工具响应返回 `{}`，持久化为 `null`，契约不一致 | `CONFIRMED` | `nodes.py:1085`、`file_storage.py:848` |
| P2-02 | 命格文档仍写 96 签一次调用，实际为 6+6 两次 | `CONFIRMED` | `destiny.py:1295-1417`、`docs/MINDSPACE_FUNCTION_MAP.md` |
| P2-03 | 命签兼容三/四列格式按位置推断人物，可能静默错绑 | `CONFIRMED` | `destiny.py:1167-1191` |
| P2-04 | 会话 JSON 投影文件名清洗后可能碰撞 | `CONFIRMED` | `file_storage.py:500,582-584` |
| P2-05 | V2 仍暴露旧档案 registry 和 profile API | `CONFIRMED` | `memory_registry.py`、`memory_update.py`、`file_storage.py:33` |
| P2-06 | InMemory 与真实 SessionRepository 字段不同 | `CONFIRMED` | `adapters/in_memory.py:129`、`adapters/file_storage.py:794` |
| P2-07 | 服务重启后 retrieval ready 状态丢失，出现预热空窗 | `RUNTIME-VERIFY` | `service.py:180,318-403` |
| P2-08 | 单轮重复读取完整会话至少三次 | `CONFIRMED` | `service.py:284,314,316,373` |
| P2-09 | 原生工具 Provider 兼容范围未向用户说明 | `CONFIRMED` | `native_tools.py:134`、`nodes.py:576-625` |
| P2-10 | Prompt inspection 可返回完整输入，缺少开发模式保护 | `RUNTIME-VERIFY` | `api.py:1428`、`prompt_inspection.py` |
| P2-11 | Core 可变端口与 Launcher 固定 `8765` 可分叉 | `RUNTIME-VERIFY` | `settings.py:95`、`desktop/main.cjs:66,727,2126` |
| P2-12 | Qwen 端口冲突策略未接入预检 | `CONFIRMED` | `qwen-runtime-policy.cjs:34`、`main.cjs:454-460` |
| P2-13 | 场景预览先更新，API 失败无回滚；上传可留孤儿资源 | `CONFIRMED` | `frontend/src/SceneExperience.tsx:69-104` |
| P2-14 | ASR 按配置存在显示，不按服务可用状态 | `CONFIRMED` | `frontend/src/App.tsx:767,3422` |
| P2-15 | 附件读取缺少异常处理和去重 | `CONFIRMED` | `frontend/src/App.tsx:3281` |
| P2-16 | 前端设置类型过宽，命格另有私有请求封装 | `CONFIRMED` | `frontend/src/types.ts`、`DestinyCanvas.tsx:109` |
| P2-17 | 两套全局 CSS 与无入口选择器并存 | `CONFIRMED` | `styles.css`、`redesign.css` |
| P2-18 | 版本来源不统一，根 `payload.json` 仍为 `0.7.2` | `CONFIRMED` | `payload.json:32`、`sync-version.mjs` |
| P2-19 | Docker 使用 Python 3.13 且不使用 `uv.lock` | `CONFIRMED` | `Dockerfile:1,12` |
| P2-20 | Docker demo 配置迁移为 openai 的条件过宽 | `RUNTIME-VERIFY` | `docker-compose.yml:7`、`product_config.py:45` |
| P2-21 | 桌面依赖大量使用 `latest` | `CONFIRMED` | `desktop/package.json:22-35` |
| P2-22 | GPT-SoVITS 音色配置存在两个手工副本 | `CONFIRMED` | `config/`、`desktop/assets/` |

## 7. P3 与废弃候选

以下内容不能直接删除，应在调用方证明和测试保护后处理。

| 候选 | 当前判断 |
|---|---|
| `presentationMode`、`resolvedPresentationMode`、`cyclePresentationMode` | 请求已固定 `auto`，属于前端死状态候选 |
| `WebTraceData` | 未发现调用点 |
| `.scene-entry`、`.presentation-entry` 等旧样式 | 当前 JSX 无入口，需 CSS 使用分析确认 |
| `ReadOnlyCapabilityService` 中旧上下文正则和 URL helper | 部分疑似旧路由残留，逐符号确认后删 |
| LLM port 的 `repair/stream_repair` | 当前图无 repair 节点；需确认外部适配器调用 |
| `shouldWaitForAsrBeforeLocalTts` | 无条件 false 且无生产调用 |
| `activeActivitySessionId` | 请求字段存在但当前前端没有设置入口，需产品确认活动功能 |
| 旧完整档案字段和 `/profiles/*` | V2 主链技术债，不可在迁移策略完成前硬删 |
| `shared_chapters`、场景、活动、日记 | 仍有生产调用，不是死代码；产品确认后再收口 |
| 旧 `410` 路由 | 兼容哨兵；需移除版本和调用方证明 |

## 8. 废弃接口与文档治理

### 8.1 接口

需要集中到 compatibility router 并标记移除版本：

- `/api/v1/characters/options`
- 旧逐角色命签接口 `/api/v1/destiny/journeys/{id}/cards/{archetype_id}`
- `character-drafts` 系列
- `characters/fate-options` 系列

需要评估 V2 后是否仅限 legacy character：

- `/api/v1/profiles/*`
- `/api/v1/profiles/*/card`
- `/api/v1/memory/registry`

### 8.2 文档

以下文档含当前与历史叙事混杂，应分类而非直接删除：

- `README.md`：仍描述旧模式大厅、旧抽卡和旧创建流程。
- `docs/CODE_READING_GUIDE.md`：同时描述 0.8.2 原生工具和旧 planner/preflight。
- `docs/APPLICATION_FULL_CHAIN.md`：部分链路仍是旧 capability 方案。
- `docs/MATURITY_HARDENING.md`：含已被替换的架构建议。
- `docs/roleplay-card-v2.md`：仍将旧 profile/runtime-state 描述为角色主体。
- `docs/structured-json-memory.md`：仍将旧 runtime state 列为活动绑定。
- `docs/MINDSPACE_FUNCTION_MAP.md`：模型调用预算过期。
- `docs/ENGINEER_HANDBOOK.md`、`docs/CODE_READING_GUIDE.md`：引用旧源目录。
- 0.5/0.6/0.7 发布验收文档：保留证据，但移动到 `docs/history/releases/` 并标记 historical。
- 原型和审阅页面：移动到 `docs/prototypes/` 或不进入生产静态目录。

文档状态统一为：`current`、`historical`、`prototype`、`report`。

## 9. 推荐模块责任边界

以下是目标边界，不要求一次大爆炸重写。

### 9.1 Python Core

| 当前文件 | 目标拆分 |
|---|---|
| `api.py` | `routers/chat.py`、`routers/characters.py`、`routers/destiny.py`、`routers/memory.py`、`routers/audio.py`、`routers/settings.py` |
| `service.py` | `conversation/orchestrator.py`、`conversation/run_registry.py`、`conversation/request_resolver.py`、`conversation/recovery.py` |
| `nodes.py` | `conversation/context_nodes.py`、`model_nodes.py`、`tool_nodes.py`、`persist_nodes.py`；图拓扑保持集中可读 |
| `prompting.py` | 唯一 `PromptBuilder`，角色、工具、成人模式、附件/互动为具名层 |
| `file_storage.py` | `session_repository.py`、`legacy_profile_repository.py`、`projection.py`、`migration.py` |
| `destiny.py` | `destiny/schema.py`、`prompts.py`、`parser.py`、`service.py`、`fallbacks.py` |
| `memory_registry.py` | `V2MemoryRegistry` 与 `LegacyRegistry` 分离 |
| `product_config.py` | 非敏感设置、凭据引用、迁移事务分离 |

### 9.2 Frontend

| 当前区域 | 目标拆分 |
|---|---|
| `App.tsx` | `ChatScreen`、`Composer`、`MessageList`、`VoiceController`、`RunInspector`、`ProfileWorkspace` |
| 请求层 | 单一 typed API client；命格、场景、聊天共用错误和认证策略 |
| SSE | 独立 run reducer，定义开始、工具、完成、中断、恢复的状态机 |
| CSS | 保留一个全局 token 层；页面样式按组件拆分，删除覆盖式双权威 |
| DTO | 从后端 OpenAPI 或共享 schema 生成，禁止 `Record<string, unknown>` 充当核心设置契约 |

### 9.3 Desktop

| 当前区域 | 目标拆分 |
|---|---|
| `main.cjs` | `process-manager`、`window-policy`、`settings-client`、`update-controller`、`storage-migration` |
| 端口 | 单一 service manifest，Launcher、脚本和 Core 只读取该来源 |
| 更新 | staging、manifest allowlist、签名验证、原子切换和回滚 |
| 凭据 | Windows 安全存储；配置 JSON 只保留 credential reference |

## 10. 加密前边界

代码保护不能替代架构、安全和数据加密。以下门槛全部满足后才能开始。

1. 生产包无 `.map`、测试脚本、真实报告、用户数据、桌面绝对路径和开发快照。
2. Core 升级为原子替换，不会残留旧未保护文件。
3. API Key 不再明文存在于 JSON、日志、报告或备份。
4. 明确代码、配置、用户数据、模型、静态 Web、运行时和诊断工具的独立 manifest。
5. 数据加密覆盖 SQLite 正文、JSON 投影、备份、知识库、Prompt inspection 和审计 payload，不能只加密主数据库。
6. 明文保留最小元数据：schema、revision、状态、时间、不可逆 owner/session 索引、任务状态和文档类型。
7. 加密前完成旧 profile/V2 边界迁移，避免把废弃字段永久固化成密文债务。
8. 保留可读错误码、版本、签名、manifest 和最小健康信息，不能让加密破坏现场诊断。
9. 密钥具有版本、轮换、备份恢复和失败回滚设计。
10. 加密层不得改变 API DTO、事务语义、幂等键和检索索引行为。

## 11. 建议分支与提交顺序

每个分支只处理一种责任，测试与代码同提交，不提交生成 Web、真实报告、运行目录或用户数据。

| 顺序 | 建议分支 | 目标 |
|---:|---|---|
| 1 | `audit/083-data-integrity` | 重生成原子语义、跨角色记忆隔离、定向 rebuild、GET 纯读 |
| 2 | `audit/083-run-state` | 同步/流式统一 durable turn、真实 Provider 请求审计、空工具契约 |
| 3 | `audit/083-prompt-boundary` | 唯一 Prompt builder、最终输入快照、性别与 V2 规则 |
| 4 | `audit/083-runtime-release` | 原子 Core 更新、进程归属、发布 allowlist、端口 manifest |
| 5 | `audit/083-secret-boundary` | DPAPI/Credential Manager、设置单写者、诊断接口保护 |
| 6 | `audit/083-frontend-contracts` | typed client、重生成元数据、SSE 恢复、场景/附件失败态 |
| 7 | `audit/083-debt-removal` | 已证明死代码、旧 CSS、compatibility router、模块拆分 |
| 8 | `audit/083-docs-ci` | 当前文档、历史归档、CI、版本单一真源和提交门禁 |
| 9 | `audit/083-encryption-readiness` | 只建立加密 manifest、威胁模型和迁移演练，不先加密业务代码 |

禁止在同一分支同时做数据迁移、UI 重设计、Prompt 改写和安装器重构。

## 12. 测试与验收门禁

### 12.1 离线强制门禁

- Python 单元与集成测试。
- 前端 TypeScript、组件交互和 SSE reducer 测试。
- Desktop Node 策略、进程归属、升级与版本一致性测试。
- OpenAPI/TypeScript DTO 契约测试。
- 敏感内容和发布包 allowlist 扫描。
- 生产包禁止 source map、内部脚本和绝对开发路径。

### 12.2 必须新增的场景

1. 互动单选、多选、普通与 NSFW 部位隔离。
2. 引用、附件、纯标签消息、重新生成和中断恢复。
3. 无工具 `null`、工具成功、失败、拒绝、SSE 重连。
4. 同 request 并发，模型、联网和任务各最多执行一次。
5. Provider 400、兼容参数、空输出、连接失败的真实请求计数。
6. 两角色同时写满偏好上限，互不淘汰。
7. 定向 rebuild 不改变其他角色任何 episode、墓碑或统计。
8. 连续 GET 命格旅程不改变 revision、选择和 final card。
9. 升级后旧 Core 独有文件消失，`data/models/environment` 保持。
10. 外部进程占用端口时更新器不得杀死外部 PID。
11. 场景绑定失败回滚、上传后绑定失败的孤儿资源处理。
12. 重启后 RAG、压缩摘要和会话 ID 持久化且不串会话。

### 12.3 真实 API

真实 API 只用于阶段验收，不进入普通 CI。使用隔离数据目录和固定角色卡，对 DeepSeek 官方模型进行一次成功闸门；达到成功条件后停止额外调用。报告只保留模型、调用次数、错误码、结构数量和脱敏摘要。

## 13. 0.8.3 完成定义

0.8.3 可以进入核心代码保护阶段，必须同时满足：

- P1 全部关闭或由产品负责人书面接受。
- P2 有明确 owner、分支和移除版本。
- 数据迁移可回滚，旧角色与旧会话可无损读取。
- 同步与流式聊天共享同一 durable turn 语义。
- Prompt 最终输入可由执行详情准确展示并通过快照。
- 发布包是正向 allowlist，升级不会残留旧文件。
- 凭据不在明文配置，生产不公开 source map。
- 当前架构、函数地图、端口、调用预算和 V2 边界文档一致。
- 新开发者可从干净分支完成安装、修改、测试和提交，不依赖历史盘符或桌面用户数据。
- 完整离线测试通过，限定真实 API 闸门通过，再单独规划代码保护实现。

## 14. 第一阶段交付结论

五路审查已覆盖主干代码、前端、桌面、脚本、测试、文档和提交污染面。第一阶段只新增本文档，没有修复业务代码，也没有把静态发现伪装成运行验收。

下一阶段应从 `P1-01` 至 `P1-04` 的数据正确性开始，再处理请求状态机和发布边界。先模块化、迁移、测试，最后才是代码保护。
