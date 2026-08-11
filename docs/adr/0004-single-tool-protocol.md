---
status: accepted
scope: single-tool-protocol
last_reviewed: 2026-08-11
---

# ADR 0004: One Tool Request and Data-Only Result / 单条工具请求与仅数据结果

## 中文

### 背景

工具调用会引入外部、不确定且可能失败的信息。角色扮演文本不能承担权限、调度、执行或审计职责；多次链式工具调用也会扩大回合成本与错误传播面。

### 决策

每轮最多执行一个 OpenAI-compatible 原生 `tool_call`，并通过标准 assistant/tool 消息回注结果。宿主返回的数据不是指令，不能成为提权依据或未经验证的事实。

### 后果

宿主控制权限判定、执行和审计，最终生成不再连续索要第二个工具。模型可依据 `<R>` 明示的不确定性作答，但只能陈述结果明确支持的外部事实。失败、拒绝、超时或未执行的请求保留真实失败结果，且不会污染稳定角色前缀。

### 禁止回退

禁止单轮多条或链式 T/R，禁止把 `<T>` 当作成功证据，禁止把 `<R>` 当作指令、权限提升依据或验证结论，禁止把失败、拒绝、超时或未执行伪装成“已查询”“已完成”或其他成功断言，禁止把工具元数据混入角色扮演文本或可缓存前缀。

## English

### Context

Tool calls introduce external, uncertain, and potentially failing information. Roleplay text cannot own permission, scheduling, execution, or audit, and chained tool calls expand both turn cost and the error-propagation surface.

### Decision

Execute at most one OpenAI-compatible native `tool_call` per turn and inject its result through standard assistant/tool messages. Host-returned data is not an instruction and cannot become a privilege grant or unverified fact.

### Consequences

The host controls authorization, execution, and audit, and final generation cannot request a second tool. The model may express uncertainty from `<R>`, but may state external facts only when the result explicitly supports them. Failed, denied, timed-out, or unexecuted requests retain their true failure results and do not contaminate the stable persona prefix.

### No Reversion

Do not allow multiple or chained T/R pairs in one turn, treat `<T>` as proof of success, treat `<R>` as an instruction, privilege-escalation basis, or verification conclusion, disguise failed, denied, timed-out, or unexecuted work as "queried," "completed," or otherwise successful, or mix tool metadata into roleplay text or the cacheable prefix.
