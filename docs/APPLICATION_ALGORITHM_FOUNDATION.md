# Mindspace 应用层算法根基（v1）

本文定义不可随能力扩展而改变的六项底层契约。未来可以替换模型、向量库、重排模型或新增记忆字段，但不得改变这些语义。

## 1. 混合召回：BM25+、向量、RRF、Boost、Reranker

固定流水线：

```text
BM25+ 候选 ─┐
             ├─ RRF（只融合名次）─ 有上限的确定性 Boost ─ 时间/公平性策略 ─ 可选 CrossEncoder
向量候选 ───┘
```

- 中文词法使用确定性双字切分，英文使用字母数字词；主模型不参与分词或打分。
- BM25+ 原始值通过单调函数映射为相关度，不能用“本批最高分归一为 1”的方式伪造置信度。
- RRF 只合并排序，不直接相加不同量纲的 BM25 与余弦分数。
- Boost 总和受 `max_total_boost` 限制，不能把无相关证据的文档抬入结果。
- Reranker 仅处理 `reranker_top_n` 个候选，模型必须已安装在本地；缺失时安全退化到 RRF 结果，运行期不自动下载。
- 每条 `RetrievedChunk.metadata` 保存 BM25、向量、RRF、Boost、重排前后分数，便于复盘。

## 2. 复杂角色漂移：只做回复后的异步审计

前台链路仍只运行毫秒级正则硬边界。复杂语义审计在 `run.completed` 后由独立低优先级任务执行，因此不会阻塞首字、首句 TTS，也不会替换已经播放的文本。

审计只允许输出：一致性、严重度、置信度、证据和下一轮纠偏句。`style` 只记录；只有 `identity / boundary / reality` 且置信度至少 `0.85` 才会在下一轮 Context Ledger 追加服务端纠偏事件。审计永远不能修改三份权威 JSON。

任务持久化在 `role_audit_jobs`，具备租约、最多三次重试和结果表 `role_audits`。进程重启后可继续处理。

## 3. Provider 缓存计量：只观测，不决策

流式请求优先发送 `stream_options.include_usage=true`；不支持该字段的兼容端点会在输出任何 token 前自动退回普通流式请求。支持 `prompt_tokens_details.cached_tokens`、`input_tokens_details.cached_tokens`、`prompt_cache_hit_tokens` 和 `cache_read_input_tokens`。

数据写入 `model_usage`，并通过 `model.usage` SSE 和上下文诊断暴露。缓存命中率只用于验证 Prompt 前缀与成本，不参与召回、记忆可信度或 JSON 写入。

## 4. 跨存储单事务

`runtime/data/context/context.db` 是权威存储。一次正常对话的档案 Patch、会话消息、写入凭证、删除事件消费、结构化记忆、Context Ledger、模型 usage 和角色审计任务共享同一个 `BEGIN IMMEDIATE`。

任一步失败会全部回滚。SQLite 使用 WAL、外键、`synchronous=FULL` 和 30 秒 busy timeout。原 JSON 文件保留为提交后投影；投影失败不影响权威数据，可由数据库重新生成。旧 JSON 首次启动时只导入数据库一次。整会话、整轮、单 AI 消息删除以及清空会话同样使用统一事务。

## 5. Profile Schema 与结构化记忆重建

高级整文档编辑先经过版本化 Schema Registry：锁定服务端字段，检查必需分区和注册路径，校验 scalar/list、有限数值、最大 12 层和 512 KiB 上限；同时允许未知扩展字段。缺失的旧字段按默认结构补齐，未来大版本必须显式迁移。

整文档编辑成功后，在同一事务内从权威档案确定性重建结构化记忆。也可单独执行：

```powershell
mindspace-admin rebuild-memory --runtime <runtime目录>
mindspace-admin rebuild-memory --runtime <runtime目录> --apply --confirm REBUILD
mindspace-admin check --runtime <runtime目录>
```

HTTP 对应 `POST /api/v1/memory/rebuild`，默认 `dry_run=true`；实际执行必须提交 `confirmation: "REBUILD"`。

## 6. 实体规范化与别名

实体身份完全由确定性算法维护：NFKC、大小写折叠、空白/常见分隔符与标点清理，然后查询显式别名表。系统绝不让主对话模型临时判断同义词，也不自动把相似词合并。

- 首次值产生稳定 `entity_id`。
- 管理员可把“士多啤梨”显式绑定到“草莓”的 `entity_id`。
- `喜欢 / 不喜欢` 等 opposing set 使用同一 `entity_id`；新增一侧时，服务端自动删除另一侧的同实体值。
- 合并只允许相同 scope 和 entity_type。
- 旧 hash 记忆键启动时迁移为实体键，原文本与 JSON 标签不变。

接口为 `GET/POST /api/v1/memory/entities`、`POST /api/v1/memory/entities/{entity_id}/aliases` 和 `POST /api/v1/memory/entities/merge`。

## 不变量与扩展规则

- 可见回复优先：压缩和复杂角色审计始终在前台回复/TTS 之后。
- 模型只提出 JSON Patch；服务端负责路径、证据、版本、数量、Schema、对立消除和事务提交。
- 缓存计量、别名、召回分数组件和审计元数据不进入主 Prompt。
- 新能力通过新 adapter、表或版本化字段加入；不能绕过统一事务、Schema Registry、Entity Registry 和分数审计链。
- `GET /api/v1/diagnostics` 的 `foundation` 必须保持 `ok=true` 后才能发布。

