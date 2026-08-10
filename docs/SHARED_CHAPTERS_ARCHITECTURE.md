> 文档状态：historical。仅保留历史证据，不得作为当前操作说明；当前权威见 `docs/INDEX.md`。

# Mindspace 0.7.2 共同篇章与会话场景架构

## 目标与边界

共同篇章把一次对话延伸为可确认、可编辑、可恢复的共同事件，但不建立第二套人物事实系统。
人物关系身份仍由角色卡中的 `relationship_label` 决定；心形状态只反映用户在该角色下收藏了多少
共同片段，不代表隐藏好感度，也不会因未登录而下降。

本期包含：

- 角色日记：用户主动生成或手写，始终是角色主观叙事。
- 共同片段：活动完成后先形成候选，用户确认后才进入时间线。
- 两种活动：默契问答、片刻故事。
- 会话场景：独立切换聊天背景，并为下一轮提供一句临时地点描述；不属于活动。
- 美术清单 v2 与按需资源包安装基础。

不包含金币、商店、衣柜、送礼、好感数值或模型直接改状态。

## 数据所有权

所有记录都以 `character_id` 为第一隔离键：

| 记录 | 生命周期 | 是否可作人物事实 | 谁能改变状态 |
| --- | --- | --- | --- |
| `JournalEntry` | draft / saved / archived | 否，`narrative_only` | 用户接口 |
| `RelationshipMoment` | candidate / saved / archived | 否，`narrative_only` | 用户确认接口 |
| `ActivityDefinition` | 静态只读 | 不适用 | 产品版本 |
| `ActivitySession` | active / interrupted / completed | 否，临时活动上下文 | 服务端 reducer |
| `ConversationScene` | 当前会话 + 角色默认继承 | 否，临时场景上下文 | 用户直接切换 |

这些记录存入 `ProductDatabase` 的独立文档命名空间，不写入 `runtime_state`。活动动作必须同时携带：

- `expected_revision`：拒绝旧页面覆盖新状态。
- `action_id`：网络重发时返回第一次处理结果，不重复推进。

中断活动会保存 `interrupted_phase`；恢复也作为一次有 revision 的动作提交。

## Prompt 链路

普通聊天不增加模型调用。携带 `activity_session_id` 时：

1. `ConversationService` 先根据聊天会话确定真实 `character_id`。
2. `SharedChapterService` 校验活动会话属于该角色。
3. 服务端构造 `ActivityPromptContext`，覆盖任何客户端自带状态。
4. `build_prompt` 在稳定角色缓存前缀之后增加动态 System 层 `activity_context`。
5. 模型只负责角色表达；阶段、选择、完成和片段创建仍由服务端 reducer 完成。

Prompt Inspector 会显示该层。其可见级别是 `ephemeral_activity_session`，并固定
`eligible_for_json_evidence=false`。

会话存在场景时，服务端另行解析 `ConversationScene`，只增加一条动态 System 消息：

```text
【当前场景】两个人现在在xxx。
```

该层标记为 `ephemeral_conversation_scene`，不会进入人物档案、长期记忆、RAG 或 JSON
写回证据，也不会增加模型调用。前端不能在聊天请求中伪造场景内容。

已保存日记和共同片段不会全量进入 Prompt，也不写入全局向量库。当前角色、当前问题命中简单
相关性匹配时，最多召回三条，标记为 `narrative_only_not_profile_evidence`。草稿、候选片段和
其他角色记录不会参与召回。

## 模型调用预算

- 普通聊天：沿用既有正文调用预算，不因共同篇章增加调用。
- 活动动作：零模型调用，完全确定性。
- 活动完成：零模型调用，使用确定性模板产生候选片段。
- 用户点击生成日记：最多一次后台正文调用；不进行第二轮协议修复。
- 日记调用失败或返回过短：同一次请求内使用本地可编辑模板。

## API

```text
GET  /api/v1/art/catalog
GET  /api/v1/art/packs
POST /api/v1/art/packs/{pack_id}/install
POST /api/v1/art/packs/{pack_id}/pause
POST /api/v1/art/packs/{pack_id}/resume

GET  /api/v1/characters/{id}/chapters/summary
GET  /api/v1/characters/{id}/journal
POST /api/v1/characters/{id}/journal
POST /api/v1/characters/{id}/journal/generate
PUT  /api/v1/characters/{id}/journal/{entry_id}
DELETE /api/v1/characters/{id}/journal/{entry_id}

GET  /api/v1/characters/{id}/moments
POST /api/v1/characters/{id}/moments
PUT  /api/v1/characters/{id}/moments/{moment_id}

GET  /api/v1/activities
GET  /api/v1/characters/{id}/activity-sessions
POST /api/v1/activities/{activity_id}/sessions
GET  /api/v1/activity-sessions/{session_id}
POST /api/v1/activity-sessions/{session_id}/actions

GET  /api/v1/scenes
GET  /api/v1/sessions/{session_id}/scene
PUT  /api/v1/sessions/{session_id}/scene
```

## 资源包安全

内置资源从 `web/archive/manifest.json` 读取。可选包只允许安装清单明确声明的 `pack_id`，
且下载源必须是解析到公网地址的 HTTPS。下载使用 `.part` 文件和 HTTP Range 续传；完整文件
必须同时通过字节数与 SHA-256 校验。

ZIP 解包限制文件数、单文件大小、总大小和路径穿越。新包先进入 staging，校验每项资产后原子
替换；替换失败会恢复旧目录。资源文件由 Core 只读暴露在 `/api/v1/art/files`。

大型模型、用户头像、私人角色卡、聊天截图和 API 凭据不属于美术资源包。

## 迁移

首次启动只迁移已有权威字段：

- `ai_profile.continuity.important_shared_experiences`
- `runtime_state.relationship_state.recent_positive_events`

迁移使用稳定摘要哈希生成片段 ID，并在一个数据库事务内写完成标记。不会根据旧聊天虚构日记，
不会迁移未确认推断，重复启动不会重复生成片段。

## 发布闸门

12 项代表性预览已于 2026-07-30 通过视觉审核，正式内置库已扩产为 171 项并替换页面临时
资源路径。当前仍保留以下发布闸门：

- 本地审核页：`/assets/archive/previews/review.html`。
- 不生成高清季节资源包。
- 不签名或上传 0.7.0 热更新。

完成高 DPI、减少动效、资源缺失降级、扩展包暂停续传和桌面真实用户闭环后，才进入签名灰度
发布。
