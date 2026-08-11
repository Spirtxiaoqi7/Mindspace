---
status: current
scope: product memory and conversation context
last_reviewed: 2026-08-11
---

# Memory and Conversation Context / 记忆与对话上下文

## 中文

### 它如何帮助对话延续

Mindspace 会把正在发生的对话分成不同层次处理，而不是把所有历史原样塞回每一次聊天：

- **短期摘要**：把已经结束的一段对话压缩为可读摘要，保留当前话题、已达成的结论和必要背景，让后续交流能自然接续，同时控制上下文长度。
- **六槽事件记忆**：近期事件会按六类线索整理：发生了什么、涉及谁、时间或场景、用户的偏好或边界、待办或承诺、情绪与关系线索。它用于理解“刚才说的那件事”，不是对用户做隐性画像。
- **长期 RAG**：长期资料通过检索按需提供相关片段，例如已保存的知识库或允许参与召回的对话记忆。系统只把与当前问题相关的内容作为参考，不会把整个长期库暴露给每一轮模型。

这三层职责不同：摘要负责连续性，事件记忆负责近期线索，长期 RAG 负责在需要时查找资料。被检索到的内容只是参考，不会因为被看到或被重复使用，就自动变成新的长期事实。

### 隔离与可信度

长期 RAG 与个人档案、角色档案和当前运行状态相互隔离。检索结果不能自行改写这些档案，也不能单独触发新的长期记忆写入。需要保存的稳定信息必须经过受控的写入和校验；当前用户明确表达的信息优先于旧的对话参考。

这意味着系统会尽量避免把猜测、临时闲聊或外部资料误当成“关于你的事实”。当旧内容与当前输入不一致时，应以用户当前的明确说明和可编辑的档案为准。

### 隐私与可编辑性

用户应当能够理解、查看和控制会被长期使用的信息：

- 可在记忆中心查看允许参与长期召回的结构化记忆，并编辑或删除它们。
- 编辑会同步更新相应的可用记忆；删除的内容不再作为活动长期记忆参与召回。
- 短期摘要和近期事件的目的只是维持当前对话，不是不可见的永久档案。
- 不带受控写入依据的普通文本不会因“被多次提到”而自动升级为长期记忆。

不同安装和版本可提供的管理入口可能不同，但产品原则不变：用户信息应当可解释、可修正、可删除；检索不是授权，也不等于自动写入。

### 给新开发者

实现或扩展时，请保持以下边界：

- 把摘要、事件记忆、结构化档案和 RAG 索引视为不同的数据层，不要用一个存储替代所有用途。
- 只让经服务端校验的受控更新进入长期结构化记忆；模型输出和检索片段都属于不可信候选。
- 召回只向提示词提供必要的相关内容，不泄露内部标签、审计信息或无关记录。
- 用户的编辑、删除和恢复操作应由确定性服务逻辑完成，而不是要求模型猜测用户意图。

## English

### How it keeps a conversation continuous

Mindspace handles an ongoing conversation in layers instead of placing every past message verbatim into every new chat:

- **Short-term summaries** condense a completed stretch of conversation into readable context: the active topic, agreed conclusions, and necessary background. This keeps the next exchange coherent while controlling context size.
- **Six-slot event memory** organizes recent events around six cues: what happened, who was involved, time or setting, user preferences or boundaries, tasks or commitments, and emotional or relationship signals. It helps the system understand “the thing we just discussed”; it is not a hidden profile of the user.
- **Long-term RAG** retrieves relevant material only when needed, such as saved knowledge-base content or conversation memories that are allowed to participate in retrieval. Only material relevant to the current request is offered as reference; the entire long-term collection is not exposed to every model turn.

The layers have separate jobs: summaries provide continuity, event memory preserves recent cues, and long-term RAG finds material when it is useful. Retrieved material is reference only. Seeing it, or seeing it repeatedly, does not automatically turn it into a new long-term fact.

### Isolation and trust

Long-term RAG is isolated from the user profile, character profile, and current runtime state. A retrieved result cannot rewrite those records by itself or independently trigger a new long-term memory write. Stable information must pass controlled writing and validation, and a user's clear current statement takes priority over older conversational reference.

This boundary helps prevent guesses, casual temporary chat, or external material from being treated as facts about the user. When older content conflicts with the current message, the user's explicit current statement and editable profiles take precedence.

### Privacy and editability

Users should be able to understand, inspect, and control information that can be used over the long term:

- The Memory Center can show structured memories that are allowed to take part in long-term retrieval, where available, and lets users edit or remove them.
- An edit updates the corresponding usable memory. Removed content no longer participates in retrieval as active long-term memory.
- Short-term summaries and recent events exist to sustain the current conversation; they are not an invisible permanent dossier.
- Ordinary text without evidence for a controlled write cannot be promoted to long-term memory simply because it is mentioned often.

Management surfaces can differ by installation and release, but the product principle remains the same: user information should be explainable, correctable, and removable. Retrieval is not authorization, and it is not an automatic write.

### For new developers

When implementing or extending this area, preserve these boundaries:

- Treat summaries, event memory, structured profiles, and RAG indexes as separate data layers. Do not substitute one store for every purpose.
- Allow only controlled updates validated by the server to create long-term structured memories. Model output and retrieved passages are untrusted candidates.
- Provide only necessary, relevant retrieved content to the prompt; do not expose internal labels, audit data, or unrelated records.
- Handle user edits, removal, and restoration through deterministic service logic, not by asking a model to infer the user's intent.
