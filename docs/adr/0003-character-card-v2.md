---
status: accepted
scope: character-card-v2
last_reviewed: 2026-08-11
---

# ADR 0003: Use Character Card V2 as Structured Role Authority / 以角色卡 V2 作为结构化角色权威

## 中文

### 背景

角色扮演需要稳定的人设，同时会话、场景、召回和用户输入都是随回合变化的数据。把角色卡降格为可随意拼接、覆盖或长期混入历史的自由文本，会削弱角色一致性并混淆权威来源。

### 决策

采用角色卡 V2 作为角色定义的结构化、版本化入口。服务端按 `character_id` 选择角色资料并完成角色、会话、场景、引用和模型上下文；稳定 persona 与角色契约进入可缓存的稳定前缀，动态场景、工具、语音与当前输入只进入动态阶段。角色专属记忆按角色边界持久化，用户基础档案与稳定偏好才可跨角色共享。

### 后果

角色定义可审计、可迁移并与会话状态分离；切换角色不会把一个角色的长期记忆或设定泄漏给另一个角色。卡片内容、服务端权威 JSON 与低信任历史、检索数据的优先级可以被明确执行。

### 禁止回退

禁止把 V2 卡片降为无结构的大段提示词，禁止由客户端输入覆盖服务端选定的角色权威状态，禁止把场景、工具、回合号或当前输入写入稳定 persona 前缀，禁止将角色专属记忆默认为全局共享。

## English

### Context

Roleplay needs a stable persona, while sessions, scenes, retrieval, and user input change per turn. Treating a character card as free text that can be casually concatenated, overwritten, or permanently mixed into history weakens character continuity and obscures authority.

### Decision

Adopt Character Card V2 as the structured, versioned entry point for character definition. The server selects the character profile by `character_id` and completes character, session, scene, reference, and model context; stable persona and role contract enter the cacheable stable prefix, while dynamic scene, tools, voice, and current input enter only dynamic phases. Character-specific memory persists within the character boundary; only the user's basic profile and stable preferences may be shared across characters.

### Consequences

Character definitions are auditable and migratable, and remain separate from session state; changing characters does not leak one character's long-term memory or settings into another. The precedence of card content, server-authoritative JSON, and low-trust history or retrieval data can be enforced explicitly.

### No Reversion

Do not collapse a V2 card into an unstructured prompt block, let client input override server-selected authoritative character state, place scene, tools, turn number, or current input in the stable persona prefix, or treat character-specific memory as globally shared by default.
