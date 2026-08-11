---
status: current
scope: prompts-tools
last_reviewed: 2026-08-11
---

# Prompt 与工具架构 / Prompt and Tool Architecture

## 中文

### 1. Prompt 的职责边界

`prompting.py` 是业务编排器：判定模式与状态、装载上下文、选择召回和动态事件、生成 `pending_events`，并返回 `PromptBuild`。`build_messages()` 是兼容入口，必须等价于 `build_prompt(...).messages`。纯文本由 `prompt_templates.py` 和 `prompt_event_templates.py` 渲染；Contributor 只把已生成内容投入固定阶段；`prompt_blocks.py` 只负责不可变块、排序、去重、渲染和审计。

模板只能接收已判定的简单值，不读取服务、存储、请求对象或模型客户端。Contributor 不得重复角色文本、重新判断业务条件或访问服务。Compiler 不理解角色扮演语义，且不得把审计元数据渲染到模型可见消息。

### 2. 顺序、缓存与可见性

Prompt 的固定阶段顺序为：`stable_prefix`、`retrieval_context`、`history_time_index`、`recent_history`、`dynamic_tail`。阶段内排序键为阶段优先级、块顺序、插入顺序。稳定 persona、角色契约和可缓存前缀不得混入场景、语音、工具、回合号、revision 或当前输入等动态数据。

最终模型消息只能是 `role` 与 `content`。`block_id`、阶段、缓存边界和审计来源仅供服务端使用。可信度由 system 契约确定：权威 JSON 状态高于低可信历史与召回。召回只带必要的来源、轮次、分数和文本，服务端内部的标签与排序元数据不得泄漏。

### 3. 模型输出与持久化边界

可见角色回复位于 `<response>`，受控数据更新位于 `<json_update>`。流式解析器只流出 `<response>` 内容；缺少起始标签时，普通自然语言仍可作为安全可见回复。完整输出结束后才解析 JSON；结构错误最多修复一次。若已有可见回复但 JSON 仍无法修复，系统保留回复，并构造 `trigger=none, patches=[]`，不写入数据。

服务端验证 `turn_id`、三份 `base_revisions`、trigger、白名单叶子路径、操作和值、证据和 Patch 上限。只有普通 `mode=primary`、非主动回复、校验通过且存在规范化 Patch 时，才允许写回。重试生成和主动回复不得写回。模型的 `analysis`、`reasoning_summary`、`state_update`、`memory_promotion` 或自造分类标签不属于协议。

### 4. 工具与 T/R 约束

工具、Skill 与 MCP 能力是本轮动态尾部数据，位于召回之后、当前用户输入之前；空能力集合不发送工具消息。工具能力不得污染可缓存的 system/persona 前缀，provider-native tool schema 也遵守同一尾部边界。

宿主仅接受 OpenAI-compatible 原生 `tools/tool_calls`，并以标准 assistant/tool 消息回注结果。失败、拒绝、超时或未执行的工具必须保留真实状态，绝不能转换成“已查询”或“已完成”。

工具结果只提供完成当前任务所需的最小数据，并保持来源与失败状态。模型可基于结果说明不确定性，但不得声称未被 `<R>` 明确支持的外部事实。工具调用、权限判定和结果审计属于宿主控制面，不属于角色扮演文本。

### 5. 故障恢复与修改规则

协议或角色确定性检查失败时，可以保留已经安全流出的正文，但必须禁止 JSON 写回。复杂语义审计在主请求完成后独立运行，只能为下一轮产生纠偏事件，不得替换已流正文或改写权威数据。

删除、重新生成和后台压缩会使旧 Context Epoch 与排队压缩失效，并从仍存在的原始会话和当前权威 JSON 重建。压缩不属于 LangGraph 主图，不阻塞 SSE 或 TTS。修改 Prompt 时必须保持公开签名、消息角色、字符与换行、阶段顺序、缓存边界和事件顺序；禁止把新的业务判断塞进模板、Contributor 或 Compiler。

## English

### 1. Prompt responsibility boundaries

`prompting.py` is the business orchestrator: it determines modes and state, loads context, selects retrieval and dynamic events, creates `pending_events`, and returns `PromptBuild`. `build_messages()` is the compatibility entry point and must equal `build_prompt(...).messages`. `prompt_templates.py` and `prompt_event_templates.py` render pure text; Contributors place already-generated content into fixed phases; `prompt_blocks.py` owns only immutable blocks, ordering, deduplication, rendering, and audit.

Templates may receive only already-decided simple values and may not read services, storage, request objects, or model clients. Contributors must not duplicate role text, re-evaluate business conditions, or access services. The Compiler does not understand roleplay semantics and must not render audit metadata into model-visible messages.

### 2. Ordering, caching, and visibility

The fixed Prompt phase order is `stable_prefix`, `retrieval_context`, `history_time_index`, `recent_history`, and `dynamic_tail`. The in-phase sort key is phase priority, block order, then insertion order. Stable persona, role contract, and cacheable prefixes must not include dynamic data such as scene, voice, tools, turn number, revision, or current input.

Final model messages may contain only `role` and `content`. `block_id`, phase, cache boundary, and audit source are server-only. Trust is defined by the system contract: authoritative JSON state outranks low-trust history and retrieval. Retrieval carries only the needed source, round, score, and text; server-internal tags and ranking metadata must not leak.

### 3. Model output and persistence boundary

The visible role reply belongs in `<response>` and controlled data updates belong in `<json_update>`. The streaming parser emits only `<response>` content; if an opening tag is absent, ordinary natural language may still be emitted as a safe visible reply. JSON is parsed only after full output; a structural error may be repaired at most once. If visible reply content already exists and JSON remains unrepairable, the system preserves the reply and constructs `trigger=none, patches=[]`, with no data write.

The server validates `turn_id`, all three `base_revisions`, trigger, allowlisted leaf paths, operation and value, evidence, and Patch limits. Writeback is allowed only for normal `mode=primary`, non-proactive replies with valid validation and normalized Patches. Regeneration and proactive replies must not write back. Model-produced `analysis`, `reasoning_summary`, `state_update`, `memory_promotion`, or invented classification labels are outside the protocol.

### 4. Tool and T/R constraints

Tools, Skills, and MCP capabilities are dynamic tail data for the current turn, after retrieval and before the current user input; an empty capability set sends no tool message. Tool capabilities must not contaminate the cacheable system/persona prefix, and provider-native tool schemas follow the same tail boundary.

The host accepts only OpenAI-compatible native `tools/tool_calls` and injects results through standard assistant/tool messages. Failed, denied, timed-out, or unexecuted tools retain their actual status and never become a claim that work was queried or completed.

Tool results provide only the minimum data needed for the current task and retain their source and failure state. The model may express uncertainty based on a result, but must not claim external facts that `<R>` does not explicitly support. Tool invocation, permission decisions, and result audit are host control-plane concerns, not roleplay text.

### 5. Failure recovery and change rules

When deterministic protocol or role checks fail, already safely streamed body text may be retained, but JSON writeback must be blocked. Complex semantic audit runs independently after the main request; it may create a corrective event for the next turn only and must not replace streamed body text or rewrite authoritative data.

Deletion, regeneration, and background compression invalidate the old Context Epoch and queued compression, then rebuild from remaining raw sessions and current authoritative JSON. Compression is outside the LangGraph main graph and does not block SSE or TTS. Prompt changes must preserve public signatures, message roles, characters and newlines, phase order, cache boundaries, and event order; new business decisions must not be placed into templates, Contributors, or the Compiler.
