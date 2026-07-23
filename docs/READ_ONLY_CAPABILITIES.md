# Mindspace 只读自动能力协议

## 权限模型

权限保存在 `settings.json` 的 `capabilities` 分组中。`master_enabled` 是总开关；
分类开关开启后，注册表内对应的只读调用自动批准，不再逐次询问。权限不包含文件修改、
Shell 执行、上传、登录、消息发送、进程控制或凭证读取。

默认启用脱敏本机状态、Mindspace 健康检查、本地知识查询和话题扩展；联网搜索、实时热点
和主动热点续接默认关闭。

## 单轮调度

```text
load_context
  -> capture_local_snapshot
  -> retrieve_knowledge / retrieve_chat
  -> rank_context
  -> capability_route
  -> plan_capabilities（仅语义不明确时）
  -> execute_capabilities
  -> compose_prompt
  -> generate_candidate
  -> parse / validate / persist
```

明确的本机或实时请求由确定性路由直接选择能力。只有“听说……是真的吗”这类无法确定
时效需求的输入才使用私有能力规划调用。规划内容不展示、不朗读、不写入会话。规划加正式
生成达到两次 LLM 调用后，协议异常会保留可见回复并禁用本轮 JSON 写回，不再发起第三次
协议修复调用。

能力触发后，服务端先发送 `capability.notice`。前端把“我去网上查一下……”和后续
`response.delta` 追加到同一个流式消息；最终 `ChatResponse.reply` 同样带有该前缀，因此
界面、会话文件、上下文账本中的内容保持一致，只形成一个 assistant message。

## 数据边界

- `local.system_snapshot`：OS、CPU、内存、GPU、Mindspace 相关进程和运行盘空间。
- `local.mindspace_health`：固定 localhost 端口的 API、ASR、GPT-SoVITS 可达性。
- `knowledge.search_local`：复用已经完成的知识、会话和结构化记忆召回。
- `web.search`：只向固定公开搜索入口发出 GET 请求，过滤非公开结果地址。
- `web.trending`：在用户话题上追加近期热点约束并进行相同的公开搜索。

本机快照每轮采集，但只有路由命中时才进入 Prompt。进程只保留允许的程序名，不读取
命令行和环境变量。网页结果标记为 `external_untrusted`；所有能力结果的
`eligible_for_json_evidence` 固定为 `false`，不能覆盖权威 JSON 或独立触发档案修改。

## SSE 事件

- `capability.routing`
- `capability.planned`
- `capability.notice`
- `capability.started`
- `capability.completed`
- `capability.failed`

以上事件共享主运行的 `run_id`、`session_id` 和 `round`。取消主运行会同时终止后续能力
和模型生成。执行详情显示能力名称、状态和时间，不展示私有规划文本或完整本机数据。
