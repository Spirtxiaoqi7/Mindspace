# 0.8.3 代码阅读指南

> 状态：current。开发源仅为 `A:\RAG\Mindspace-admin`。

## 从入口阅读

1. `src/mindspace_graph/api.py`：HTTP/SSE 入口、模型列表和请求边界。
2. `src/mindspace_graph/service.py` 与图模块：durable run、会话装载、上下文、生成和终态持久化。
3. `src/mindspace_graph/destiny.py`：V7 旅程、8 方向、命签 6+6、选择、V2 合成与提交。
4. `frontend/src/api.ts`、聊天状态与页面组件：浏览器请求、SSE、provider/tool attempt 展示。
5. `desktop/preload.cjs`、设置桥、端口/更新管理模块：桌面权限与 Core 启动边界。

## 0.8.3 拆分后的精确入口

- API 装配：`src/mindspace_graph/api.py` 只创建共享服务并注册 `src/mindspace_graph/api_routes/` 下的 `chat_runs.py`、`characters_cards.py`、`destiny_routes.py`、`system_settings.py`、`memory_knowledge.py`、`audio_scenes.py` 与 `legacy_routes.py`。
- Durable run：`src/mindspace_graph/conversation_runs.py` 负责 run、SSE envelope、重放、加入与孤儿终态；业务编排仍从 `service.py` 进入 `graph.py`。
- 前端聊天：`frontend/src/chat/useConversation.ts` 管理提交和恢复，`Composer.tsx` 管理附件/互动/ASR/模型选择，`ExecutionInspector.tsx` 只展示真实 attempts。
- 前端设置：`frontend/src/settings/SettingsWorkspace.tsx` 通过 `frontend/src/api.ts` 与 desktop preload 桥通信。
- 前端角色：`frontend/src/characters/CharacterExperience.tsx` 负责角色库与入口，V7 画布仍位于 `frontend/src/DestinyCanvas.tsx`。
- Desktop controllers：`desktop/settings-controller.cjs`、`update-controller.cjs`、`service-supervisor.cjs` 分别承担设置秘密、更新和服务生命周期；`main.cjs` 只组合，`preload.cjs` 只暴露窄 IPC。

完整逐文件导航由 `scripts/generate-codebase-index.mjs` 生成，见 `docs/CODEBASE_INDEX_0.8.3.md` 与 `docs/CODEBASE_FILE_INDEX_0.8.3.md`。

## 当前关键不变量

- 普通聊天只有当前模型回复；原生工具请求最多执行一次，并持久化 attempt。
- 无工具调用的历史回合不得恢复出空工具卡。
- 命签前后 6 类是两个模型调用、一个可见阶段，已成功半批必须保留。
- provider attempt、durable run 和会话摘要都按会话/回合 ID 隔离。
- V2 是角色权威格式；历史 profile/scene/presentation 接口是否删除以废弃清单门禁为准。

## 修改前定位

先用 `rg` 从路由、SSE 事件名或状态字段追到消费者，再改最小职责分支。不要从历史设计文档推断当前实现；文档状态以 `docs/INDEX.md` 为准。
