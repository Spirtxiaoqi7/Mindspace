# Mindspace 只读自动能力协议

## 权限模型

权限保存在 `settings.json` 的 `capabilities` 分组中。`master_enabled` 是总开关；
分类开关开启后，注册表内对应的只读调用自动批准，不再逐次询问。权限不包含文件修改、
Shell 执行、上传、登录、消息发送、进程控制或凭证读取。

默认启用本地知识查询和话题扩展；联网搜索、实时热点和主动热点续接默认关闭。
AI 工具注册表不提供硬件、进程、端口或服务健康查询。Launcher 的启动探测和诊断接口
仍然独立工作，但不会进入对话模型的可调用能力。

## 单轮调度

```text
load_context
  -> retrieve_knowledge / retrieve_chat
  -> rank_context
  -> capability_route
  -> plan_capabilities（仅语义不明确时）
  -> execute_capabilities
  -> compose_prompt
  -> generate_candidate
  -> parse / validate / persist
```

明确的链接、联网、时效或本地知识请求由确定性路由直接选择能力。只有“听说……是真的吗”
这类无法确定时效需求的输入才使用私有能力规划调用。规划内容不展示、不朗读、不写入会话。

能力触发后，服务端先发送 `capability.notice`。前端把“我去网上查一下……”和后续
`response.delta` 追加到同一个流式消息；最终 `ChatResponse.reply` 同样带有该前缀，因此
界面、会话文件、上下文账本中的内容保持一致，只形成一个 assistant message。

## 数据边界

- `knowledge.search_local`：复用已经完成的知识、会话和结构化记忆召回。
- `web.open`：打开用户给出的公开 HTTP(S) 页面并读取正文。
- `web.search`：只向固定公开搜索入口发出 GET 请求，过滤非公开结果地址。
- `web.trending`：在用户话题上追加近期热点约束并进行相同的公开搜索。

普通零调用轮不向主模型注入工具目录、能力设置或 `call_count=0` 消息。只有实际发生查询时
才注入精简执行状态和查询结果；联网真实性同时由生成后的确定性校验保证。网页结果标记为
`external_untrusted`；所有能力结果的
`eligible_for_json_evidence` 固定为 `false`，不能覆盖权威 JSON 或独立触发档案修改。

## SSE 事件

- `capability.routing`
- `capability.planned`
- `capability.notice`
- `capability.started`
- `capability.completed`
- `capability.failed`

以上事件共享主运行的 `run_id`、`session_id` 和 `round`。取消主运行会同时终止后续能力
和模型生成。执行详情显示能力名称、状态和时间，不展示私有规划文本。
