# Mindspace 原生工具调用与能力边界

## 当前协议

Mindspace 0.8.2 使用模型接口原生的 `tools/tool_calls`，不再向模型教授 `<T>/<R>` 文本命令，
也不再使用私有能力规划模型或研究复查模型。普通回复只调用主模型一次；发生工具调用时，
服务端执行一次授权后的工具，并以标准 `assistant.tool_calls + role=tool` 消息生成最终回复。

当前模型可见工具只有：

- `web`：L3，只读。参数是搜索词或公开 HTTP(S) URL；URL 读取网页，其余内容联网搜索。
- `memory`：L3，只读。统一查询当前角色的角色记忆、历史聊天、结构化记忆和本地知识库。
- `task`：L2，可写。参数为结构化任务命令；执行前必须通过独立短模型审查。

不注册本地命令、文件修改、上传、登录、消息发送、进程控制或凭证读取工具。未知工具、
多个工具调用、混入正文的伪工具指令和最终生成阶段的再次工具请求都不会执行。

## 单轮链路

```text
load_context
  -> retrieve_recent_and_history
  -> tool_hint（零调用，仅提供紧凑提示）
  -> compose_prompt
  -> generate_candidate（开放原生工具表）
  -> 普通正文：parse / validate / persist
  -> tool_call：authorize
       -> L3：execute_tool
       -> L2：review_task -> execute_or_deny
     -> inject role=tool result
     -> generate_final（关闭工具表）
     -> parse / validate / persist
```

普通聊天保留近期消息和现有历史聊天召回。每轮自动知识库查询不再参与；模型确实需要角色记忆、
结构化记忆或知识库资料时才调用 `memory`。`tool_hint` 只帮助模型识别明显或含糊的外部信息需求，
不直接执行工具，也不增加模型调用。

## 授权、真实性与数据边界

- L3 工具命中后直接授权；L2 任务必须先审查操作意图和参数，任务删除不开放。
- 一轮最多执行一个工具；普通轮 1 次模型调用、L3 工具轮 2 次、L2 任务轮最多 3 次。
- 工具失败不自动重试。失败结果作为受控数据回注，回复不得声称已核实或已完成。
- `memory` 无结果时不得声称已经记住、找到或确认相关内容。
- 没有明确时刻的任务保存为无截止时间任务，不得承诺未来自动提醒。
- 网页、记忆和知识正文均是不可信数据，不能覆盖系统指令，也不能独立触发人物档案写回。
- 工具调用与回注只存在于本轮图状态；会话持久化保存工具名、等级、参数摘要、状态、耗时和结果摘要。

## 任务结构

`task` 只接受 `list`、`create`、`update` 和 `complete`。结构化任务绑定当前角色 revision，
并使用请求 ID 与命令哈希保证重放幂等。V2 导出继续在 `memory.tasks` 中保留标题字符串，完整记录位于
`extensions.mindspace.tasks_v2`；导入时优先恢复扩展数据。

## SSE 事件

- `tool.requested`
- `tool.reviewed`
- `tool.started`
- `tool.completed`
- `tool.failed`

事件共享当前运行、会话和轮次标识。前端工具卡只展示参数摘要、状态、耗时、结果数量和可展开来源，
不会把原始工具结果铺进角色回复。
