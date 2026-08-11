---
status: current
scope: storage-memory
last_reviewed: 2026-08-11
---

# 存储与记忆架构 / Storage and Memory Architecture

## 中文

### 1. 权威性与职责

Mindspace 的持久化以聚合仓储为边界。`JsonProfileRepository` 负责用户档案、AI 档案、角色记忆和运行状态；`JsonSessionRepository` 负责会话、消息、轮次、删除、检索分块与旧会话兼容。组合根 `bootstrap.py` 直接装配这些物理实现；新生产代码不得依赖 `adapters.file_storage`，后者仅为历史导入提供重导出兼容。

启用 `ProductDatabase` 时，数据库提交是权威写入路径，文件投影只能在提交后执行。不得以先写 JSON、后提交数据库替代该顺序。未启用数据库的文件模式仍通过仓储保持同一文档、锁、revision、备份和迁移语义。

### 2. 数据路径与格式不变量

运行根目录由部署层提供，仓储不得自行推断或把用户数据写入源码、安装包目录。默认运行数据包括：

- 权威档案：`data/profiles/*.json`
- 档案历史：`data/profiles/history/{target}/*.json`
- 原始会话：`data/sessions/*.json`
- Context 账本：`data/context/context.db`
- 写入凭证：`data/memory-write-receipts.json`
- 删除校正事件：`data/memory-deletion-events.json`
- 结构化记忆：`data/structured-memory.json`
- 知识库：`data/knowledge.json`
- 设置与审计：`config/settings.json`、`logs/events.jsonl`

JSON 使用 UTF-8、`ensure_ascii=False` 和两个空格缩进。原子写必须在目标目录创建临时文件，刷新并 `fsync` 后以 `os.replace()` 替换，且不吞没文件系统或 JSON 异常。Profile 文件名由 `TARGET_FILES` 决定；Session 使用安全前缀加原始 session ID 的完整 SHA-256。旧 Session 路径只可在归属校验后兼容读取或迁移。

### 3. 并发、版本与迁移

仓储持有业务锁；底层路径、JSON I/O、Patch 和时间 helper 不持锁，也不决定事务边界。写入前必须检查 revision；stale revision 必须拒绝。恢复历史版本必须生成新的 revision，不能回退权威版本号。

写前备份、恢复前备份、`copy2()` 元数据保留以及异常传播均属于数据协议。Legacy 导入必须幂等，不能覆盖已提交数据；analysis 清理和路径迁移必须先备份，并持续保留旧路径兼容，直到存在独立、可恢复、版本化的迁移。

### 4. 结构化记忆与召回

三份权威档案为 `user_profile`、`ai_profile` 和 `runtime_state`。服务端管理 `schema_version`、`profile_type`、`revision`、`updated_at`；模型 Patch 不得改写这些字段。允许写入的叶子字段、作用域、生命周期、容量和冲突族只由 `memory_registry.py` 定义。

`structured-memory.json` 分为 `episodes`、`active`、`untagged` 和 `tombstones`。只有成功 JSON Patch 的写入凭证才能把原始 episode 绑定为活动结构化记忆；绑定复用同一原文和向量。无成功 Patch 的普通文本仅进入有界、过期的 `untagged` 隔离池，不进入召回，也不得因曝光升级为长期记忆。对立偏好和规则按显式冲突族与规范化值消除，不以语义相似度猜测冲突。

模型只接收获召回 episode 的必要来源、轮次、分数和文本；JSON 标签、`memory_key`、曝光统计及字段族只用于服务端审计和排序。

### 5. 失败与恢复

写入或事务失败时，不得留下被误认为已提交的文件投影；异常必须向既有降级或调用边界传播。Patch 校验失败可保留对话回复，但不得写入权威 JSON 或生成活动记忆绑定。

删除 AI 回复时，先移除关联凭证、episode 与活动 binding，权威档案维持当前值，并记录待处理删除事件。下一次普通 primary 对话才可通过有效的删除校正 Patch 处理该事件。取消、重试生成、主动回复或无效 Patch 不得消费删除事件。恢复应优先使用仓储的历史备份和受控 revision 路径，不得手工覆盖运行数据。

## English

### 1. Authority and responsibilities

Mindspace persistence is bounded by aggregate repositories. `JsonProfileRepository` owns user profiles, AI profiles, character memory, and runtime state; `JsonSessionRepository` owns sessions, messages, turns, deletion, retrieval chunks, and legacy-session compatibility. The composition root, `bootstrap.py`, wires these physical implementations directly. New production code must not depend on `adapters.file_storage`; it remains only as a re-export compatibility surface for legacy imports.

When `ProductDatabase` is enabled, the database commit is the authoritative write path and file projection may occur only after that commit. Writing JSON first and committing the database second must not be used as a substitute. File mode without the database still preserves the same document, lock, revision, backup, and migration semantics through the repositories.

### 2. Data-path and format invariants

The deployment layer supplies the runtime root. Repositories must not rediscover it or write user data into source or installation directories. Default runtime data includes:

- Authoritative profiles: `data/profiles/*.json`
- Profile history: `data/profiles/history/{target}/*.json`
- Raw sessions: `data/sessions/*.json`
- Context ledger: `data/context/context.db`
- Write receipts: `data/memory-write-receipts.json`
- Deletion-reconciliation events: `data/memory-deletion-events.json`
- Structured memory: `data/structured-memory.json`
- Knowledge base: `data/knowledge.json`
- Settings and audit: `config/settings.json`, `logs/events.jsonl`

JSON uses UTF-8, `ensure_ascii=False`, and two-space indentation. Atomic writes must create a temporary file in the target directory, flush and `fsync` it, then replace with `os.replace()`, without swallowing filesystem or JSON exceptions. Profile names are defined by `TARGET_FILES`; Sessions use a safe prefix plus the full SHA-256 of the original session ID. A legacy Session path may be read or migrated only after ownership validation.

### 3. Concurrency, versions, and migration

Repositories own business locks. Low-level path, JSON I/O, Patch, and time helpers own no locks and do not define transaction boundaries. A revision must be checked before writing; a stale revision must be rejected. Restoring history must produce a new revision and must never roll back the authoritative version number.

Pre-write backup, pre-restore backup, `copy2()` metadata preservation, and exception propagation are all data-protocol behavior. Legacy import must be idempotent and must not overwrite committed data. Analysis cleanup and path migration must back up first and retain legacy-path compatibility until an independent, recoverable, versioned migration exists.

### 4. Structured memory and retrieval

The three authoritative profiles are `user_profile`, `ai_profile`, and `runtime_state`. The server manages `schema_version`, `profile_type`, `revision`, and `updated_at`; model Patches must not overwrite them. The allowed writable leaf fields, scope, lifecycle, capacity, and conflict families are defined solely by `memory_registry.py`.

`structured-memory.json` contains `episodes`, `active`, `untagged`, and `tombstones`. Only a write receipt from a successful JSON Patch may bind a raw episode as active structured memory; bindings reuse the same source text and vector. Ordinary text without a successful Patch enters only the bounded, expiring `untagged` quarantine, is not retrieved, and must not be promoted to long-term memory through exposure. Opposing preferences and rules are resolved by explicit conflict families and normalized values, not semantic-similarity guesses.

The model receives only the necessary source, round, score, and text of retrieved episodes. JSON tags, `memory_key`, exposure statistics, and field families remain server-side for audit and ranking.

### 5. Failure and recovery

A write or transaction failure must not leave a file projection that appears committed; the exception must reach the established fallback or caller boundary. A failed Patch validation may preserve the conversational reply, but must not write authoritative JSON or create an active-memory binding.

When an AI reply is deleted, first remove its associated receipt, episode, and active binding, keep authoritative profiles at their current values, and record a pending deletion event. Only a later normal primary conversation may address that event with a valid deletion-reconciliation Patch. Cancellation, regeneration, proactive replies, or invalid Patches must not consume the event. Recovery should use repository history backups and the controlled revision path, never manual overwrites of runtime data.
