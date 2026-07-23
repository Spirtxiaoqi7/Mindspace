# 七项成熟化改造说明

本文记录 0.5.7 的成熟化改造边界。目标是减轻单轮开销、阻止低可信数据升级为事实，并让流式运行可恢复；不改变人物设定、对话风格或无关业务。

## 1. 模型调用预算

入口位于 `src/mindspace_graph/nodes.py` 的 `NodeFactory.CALL_BUDGETS`。每轮按用途独立计数：

| 用途 | 上限 |
| --- | ---: |
| `planner` | 1 |
| `research_review` | 1 |
| `generation` | 1 |
| `protocol_repair` | 1 |
| `memory_extract` | 1 |
| 合计 | 5 |

普通对话不经过能力规划，只调用一次 `generation`。预算耗尽的非正文工作直接降级，不循环重试。响应兼容保留 `llm_call_count`，并在 `model.call_summary` 中给出用途、耗时、状态和错误摘要。

时间词不能单独构成联网意图：例如“今天心情不错”保持普通对话；“今天北京天气”同时具有时效词和信息主题，才进入只读联网能力。正文已经能够确定性提取时，服务端直接补成无写回的安全协议，不再为标签或 JSON 格式调用 `protocol_repair`；只有正文也无法恢复时才允许一次修复调用。

只读外部能力由 `ReadOnlyCapabilityService.execute()` 按计划顺序串行执行。工具内部可以复用一个 HTTP 客户端；一个搜索调用读取多个公开页面属于该工具内部实现，不允许多个工具同时写图状态。

## 2. 流式运行与恢复

`ProductDatabase` 使用以下表：

- `conversation_runs`：运行身份、状态、部分文本和最新序号；
- `conversation_run_events`：里程碑事件，单轮最多 128 条；
- `prompt_inspections`：只保存 Prompt 哈希和层级长度。

正文每 500 毫秒或累计 1 KiB 做一次 checkpoint。浏览器将当前 `run_id` 放入 `localStorage`，刷新后只重新订阅 `GET /api/v1/runs/{run_id}/stream`，不会重新发送聊天 POST。

Core 启动时将旧进程遗留的 `running` 标记为 `interrupted`，先发送 `response.replace` 恢复 checkpoint，再发送 `run.interrupted`。前端保留文字并显示“回答在此处中断”，不自动继续生成。

## 3. 上下文可信等级

所有上下文写入统一经过 `ContextLedger._insert_event()`，并具有：

- `source`：用户、助手、服务端观察等来源；
- `confidence`：确定性置信值；
- `visibility`：`model`、`audit` 或 `ephemeral`。

确认的用户输入、最终助手回复和批准的档案修订可进入长期模型上下文。检索候选、工具结果、调度状态、能力统计和研究计划只保留审计，不在下一轮 Prompt 或向量召回中出现。ASR 低置信候选和停用的情绪状态只允许当前轮临时使用。

## 4. 用户直接编辑档案

人物卡和侧栏均可进入同一档案编辑器。保存接口是 `PUT /api/v1/profiles/{name}`；服务端校验 schema 和提交的 revision，成功后递增 revision、记录 `user_direct_profile_edit` 并重建相关记忆索引。

版本接口：

- `GET /api/v1/profiles/{name}/history`
- `POST /api/v1/profiles/{name}/restore`

恢复旧版本会生成新的 revision 并保留恢复前版本。AI JSON Patch 仍受最新 revision 约束，不能覆盖用户刚保存的内容。

## 5. 模型实际输入检查器

接口：

- `GET /api/v1/runs/{run_id}/prompt-inspection`
- `GET /api/v1/runs/{run_id}/prompt-inspection?reveal=true`

完整 Prompt 只在 `PromptInspectionStore` 内存中保留最多 10 轮、30 分钟。默认响应隐藏内容；磁盘只保存 SHA-256、角色、层名、字符数和 token 估算。检查器记录的是已经构造完、即将交给正文模型的同一份 `messages`，读取检查器不会改写模型输入。

## 6. ASR 仲裁和限频

确定性仲裁位于 `streaming_asr.apply_asr_decision()`，不调用额外模型。

1. `asr.speech_candidate` 只将 TTS 音量降到 25%。
2. `asr.speech_start` 只说明 VAD 成立，仍不停止 TTS、不取消运行。
3. 明确停词或稳定 partial 通过 VAD 与回声排除后，才发送 `asr.barge_in_confirmed`。
4. `asr.final` 输出 `quality`、`confirmed_text`、`uncertain_segments`、`barge_in_eligible` 和 `decision_reasons`。
5. 只有可靠主干才能提交 LLM；纯低置信内容只显示为可编辑草稿。

普通有效打断冷却 1.5 秒；明确停词可绕过冷却，但不能绕过 VAD 和回声判断。同一规范化文本 3 秒内只提交一次。误候选使播放期门限临时提高 3 dB/120 ms，最多叠加到 6 dB/240 ms，3 秒后恢复。

低置信片段仅通过 `input_evidence.asr` 进入当前轮尾部，并附带“不得视为用户事实”的规则。正式用户消息、人物档案、向量库和长期记忆只保存 `confirmed_text`。

## 7. 情绪接口

情绪模型继续停用，不加载模型、不占用显存。保留接口和未来接入位置详见 [EMOTION_INTERFACE.md](EMOTION_INTERFACE.md)。

## 验收命令

在 PowerShell 7 中执行：

```powershell
Set-Location A:\RAG\langgarph-rag
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
Set-Location frontend
npm test -- --run
npm run build
```

源码验证全部通过前，不得覆盖 `A:\Mindspace\application\core`。
