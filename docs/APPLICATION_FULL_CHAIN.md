# Mindspace 0.8.3 全链路

> 状态：current。本文是产品请求链的权威说明，不描述已废弃 planner/research/repair 方案。

## 桌面启动与设置

桌面 Launcher 从受控设置桥读取本地 provider 配置，按 `config/service-ports.json` 解析服务端口并启动 Core。前端只能通过 preload 暴露的窄接口修改设置；密钥不得进入页面日志、SSE、报告或更新资产。

## V7 命格

`创建角色 -> 保存种子 -> 生成 8 个方向 -> 前 6 类命签 -> 后 6 类命签 -> 12 节点选签 -> V2 合成 -> 入库 -> 本地聊天`。

96 张命签在 UI 上仍是一次“生成命签”，内部是两次独立调用。每半批有独立状态和错误；一半成功、一半失败时保留成功结果，只重试失败半批。两半完整且笛卡尔积通过后才能进入画布。

## 聊天请求

1. API 校验角色、会话、引用、附件、互动标签与模型设置。
2. 创建或加入 durable run，并载入当前会话档案、近期上下文、持久摘要及允许的召回结果。
3. 主模型生成普通回复或一条原生工具指令。
4. 工具授权/执行结果以 attempt 保存；工具结果仅作为本轮数据注入，失败不伪装成功。
5. provider 的每次尝试均记录模型、状态和错误摘要；最终回复与 run 终态在返回前持久化。
6. SSE 重连或页面刷新按 run/turn ID 恢复，不跨会话，不生成空工具卡。

实际代码路径是：`frontend/src/chat/useConversation.ts` -> `src/mindspace_graph/api_routes/chat_runs.py` -> `src/mindspace_graph/conversation_runs.py` -> `src/mindspace_graph/service.py` -> `src/mindspace_graph/graph.py`。`api.py` 只负责装配，不再承载全部路由实现。

## 角色、设置与桌面控制器

- 角色与 V2 卡：`frontend/src/characters/CharacterExperience.tsx` -> `api_routes/characters_cards.py` -> character/session stores。
- 设置与模型：`frontend/src/settings/SettingsWorkspace.tsx` -> `frontend/src/api.ts` -> `desktop/preload.cjs` / `desktop/settings-controller.cjs` -> `api_routes/system_settings.py`。
- 桌面运行：`desktop/main.cjs` 组合 `service-supervisor.cjs`、`update-controller.cjs` 和窗口策略；业务状态不应回流到 Launcher。
- V7：`frontend/src/DestinyCanvas.tsx` -> `api_routes/destiny_routes.py` -> `destiny.py`，前后 6 类半批状态均由旅程持久化。

## 发布与资产

产品版本来自 `config/version.json`。Core 发布按 allowlist 复制；生产资产禁止 source map。GPT-SoVITS 音色表以 `config/gpt-sovits-voices.json` 为权威，desktop 文件只是生成镜像。bootstrap manifest 只在正式打包前由 `desktop/prepare-bootstrap.cjs` 生成。
