# Mindspace

> 当前源码版本：**0.5.18**
> 面向 Windows 的本地优先 AI 角色陪伴框架，使用 LangGraph 编排对话、检索、工具、记忆、档案与语音链路。

Mindspace 将模型调用、RAG、结构化人物档案、长期记忆、ASR、TTS 和桌面 Launcher 组合成一套可检查、可恢复、可扩展的应用框架。项目重点不是“把所有内容都塞进 Prompt”，而是明确每类信息的来源、可信等级、生命周期和写入权限。

## 0.5.18 重点

- 本地 TTS 请求在 Core 内串行排队，尚未进入模型的请求可直接取消，不会在
  GPT-SoVITS 后台堆积并延迟后续回复。
- ASR 模型未加载完成或连接中断时，语音页以最高 5 秒间隔持续恢复；取得麦克风前
  先检查 Worker 就绪，退出语音后立即停止恢复。
- TTS 冷启动期间最多等待 90 秒，不再把 Launcher 正在加载模型误报为连接失败。
- TTS 分段使用“首句优先、括号独立、正文整段”：首句尽快播放，括号内容单独播放，
  其余正文跨句跨段合并到下一个括号或轮末。
- Launcher 异步校验 ASR CUDA 依赖，Torch 冷启动期间窗口仍能刷新和停止，
  不再因同步导入进入“未响应”。

## 0.5.17 重点

- ASR、TTS 或 Core 子进程意外退出后，由 Launcher 按 `1s / 2.5s / 5s`
  有界退避自动拉起，连续失败不会无限重启。
- 实时 ASR 断线自动重连最多 4 次；重连期间释放旧麦克风、Worklet 和
  WebSocket，用户仍可随时退出语音。
- TTS 响应、首包、流中断、播放器启动和播放结束均有独立看门狗，
  任一路径失败都会清空队列并解除输入状态。
- GPT-SoVITS 遇到英文片段时不再依赖未打包的 NLTK 词性资源；
  客户端正常取消流也不会触发二次异常写回。
- 音量动画限制为每秒最多 20 次 React 更新，避免语音高频事件拖慢点击响应。

## 0.5.16 重点

- 最近 8 轮原始对话直接进入模型，保障当前交流连续性。
- 更早原文不再常驻 Prompt，只在语义命中时由 RAG 选择性召回。
- RAG 只对最近 8 轮直接历史去重，不会再误删旧轮命中。
- 完整会话仍保留在数据库和检索索引中；召回候选维持低可信等级。

## 0.5.15 重点

- 完整原始聊天继续保存在数据库和页面，模型每轮只接收最近 8 个 round。
- 历史窗口只含可见用户原话和最终助手正文，不混入角色审计、JSON Patch、
  删除校正或主动续话状态机占位。
- 历史窗口独立于上下文压缩阈值，未达到 65% 也不会继续累计 44 轮原文。
- 最新真实会话的历史层由约 10,439 字符降至 1,819 字符。

## 0.5.14 工具与 Prompt 精简

- AI 不再拥有本机硬件、进程、端口或服务健康状态查询工具。
- Launcher 健康探测与诊断接口保持独立，不进入对话 Prompt。
- 普通聊天不再携带工具目录、能力设置或零调用状态消息。
- 实际查询轮仅携带精简执行状态和查询结果，服务端继续硬性阻止虚假联网声明。
- 当前可调用能力精简为本地知识、打开网页、网页搜索和热点检索。

## 0.5.13 角色演绎与 Prompt 减负

- 当前用户输入之后增加临时 System 角色演绎校准，直接约束本轮下一次回应。
- 角色自主与角色主动分离：意愿一致时直接推进，不用反复询问或中性闲聊打太极。
- 角色卡中的情境规则依据当前场景、近期对话和运行状态确定性激活。
- RAG 不再重复发送已经位于原始历史中的同一聊天消息。
- 普通闲聊只保留精简能力真实性状态，不再携带完整工具目录。

## 0.5.12 语音完整朗读

- 实时语音完整朗读括号中的动作、神态、触感与语气内容。
- 流式回复等待拆分括号闭合后再分句，不漏读后续 token。
- 普通文字页朗读仍过滤括号内容，改动只作用于实时语音。

## 0.5.11 角色自主性

- AI 人物 JSON 作为首条 System 内的权威角色卡加载，角色首先忠于自身设定。
- 当前聊天不能永久改写角色；只有用户在 AI 档案编辑器中保存的新版本才生效。
- 角色可自然表达赞同、分歧或拒绝，不再把顺从和即时取悦作为最高目标。
- 沉浸互动允许直接描述角色自己的穿着、动作、距离和触感，不再强制改写成假设。
- AI 角色卡不在数据层重复注入，减少同一份 JSON 的重复 Prompt token。

## 0.5.10 语音可靠性

- 语音入口增加 ASR、TTS 与音频上下文就绪检查，并可随时取消悬挂请求。
- 整次语音会话复用播放上下文，增加 TTS 首包、空音频与播放器启动看门狗。
- 中文续话使用自适应 `0.65–1.7 秒` 聚合窗口，插话后至少等待 `1.5 秒`。
- VAD、持续时间与回声排除成立时，“不对”“不是”等短插话可以正常生效。

## 0.5.9 用户角色卡与第一认同性别

- 用户档案与 AI 档案新增“男 / 女”第一认同性别选择。
- 性别由用户直接保存，模型 JSON Patch、档案初始化和记忆抽取不能修改。
- 主模型第一条 System 内容先声明双方性别，再加载角色、权威 JSON、历史和工具上下文。
- 本地结构化用户角色卡可通过档案 API 写入并生成 revision 与记忆索引；私人档案不进入源码或安装包。

## 0.5.8 通话与面对面互动

- 实时语音入口新增“通话 / 面对面”选择，默认保持原通话逻辑。
- 面对面模式可保存当前场景，并在后续每轮语音中持续加载。
- 面对面场景通过临时高优先级 Prompt 层提供现场感，不作为人物事实、长期记忆或 JSON Patch 证据。
- `interaction.voice_entry_mode` 与 `interaction.face_to_face_scene` 保存用户上次选择和内容。

## 0.5.7 成熟化改造

- 模型调用按 `planner`、`research_review`、`generation`、`protocol_repair` 和 `memory_extract` 独立计数，单轮总上限为 5。
- 普通闲聊只进行正文生成；时间词本身不会误触联网规划。
- 流式正文按检查点持久化，页面刷新、SSE 断线和 Core 重启都不会自动重复生成。
- 检索候选、工具结果和调度状态仅用于审计，不会升级为长期用户事实。
- 用户可以直接编辑人物档案，支持 revision 冲突保护、版本历史和恢复。
- Prompt Inspector 可解释某轮模型实际接收的规则、档案、历史、检索与裁剪结果。
- ASR 使用确定性三阶段仲裁，低置信内容不会误停 TTS、误调 LLM 或写入长期记忆。
- 情绪模型保持关闭，不占用显存，仅保留未来接口边界。

完整版本记录见 [CHANGELOG.md](CHANGELOG.md)，机器可读记录位于 [docs/release-history.json](docs/release-history.json)。

## 核心链路

```mermaid
flowchart LR
    UI["React / Electron"] --> API["FastAPI + SSE"]
    API --> GRAPH["LangGraph Turn Graph"]
    GRAPH --> CONTEXT["可信上下文与人物档案"]
    GRAPH --> RAG["知识库 / 会话 / 结构化记忆"]
    GRAPH --> CAP["只读外部能力"]
    GRAPH --> LLM["模型调用预算"]
    ASR["FunASR Worker"] --> ARB["ASR 仲裁器"]
    ARB --> API
    API --> TTS["云端 TTS / CosyVoice / GPT-SoVITS"]
    GRAPH --> DB["JSON + SQLite 审计与运行恢复"]
```

一轮对话的真实节点、条件边、Prompt 顺序和模型 HTTP 输入，可从 [代码精读指南](docs/CODE_READING_GUIDE.md) 开始阅读。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `src/mindspace_graph/` | LangGraph、API、Prompt、RAG、档案、上下文、ASR/TTS 适配 |
| `frontend/` | 对话产品界面和 Prompt Inspector |
| `desktop/` | Electron Launcher、组件安装、更新和故障诊断 |
| `tests/` | 后端单元、集成、恢复、可信分层和中文场景测试 |
| `scripts/` | 启动、验证、打包、更新和运行时准备脚本 |
| `docs/` | 架构、算法、调用链、运行手册和版本化设计文档 |
| `vendor/` | 必需的第三方语音代码或适配器；模型权重不在仓库内 |

以下内容不会上传到 Git：

- API 密钥、签名私钥和用户配置；
- 会话、人物档案、日志、数据库和下载缓存；
- Python/Node 私有环境；
- ASR、TTS、向量和角色音色模型；
- 安装包、blockmap、Core ZIP 与 Electron 解包目录；
- 本地参考音频和声音候选。

## 开发环境

建议环境：

- Windows 10/11 x64；
- PowerShell 7；
- Python 3.11；
- [uv](https://docs.astral.sh/uv/)；
- Node.js 20 或更高版本；
- 本地 ASR/TTS 可选 NVIDIA GPU，纯文字与云端接口不要求 GPU。

克隆时初始化第三方子模块：

```powershell
git clone --recurse-submodules https://github.com/Spirtxiaoqi7/Mindspace.git
Set-Location .\Mindspace
```

安装后端和前端开发依赖：

```powershell
uv sync --extra dev --extra embeddings
npm --prefix frontend ci
npm --prefix desktop ci
```

默认 `demo` 模式不需要 API Key。启动 Core：

```powershell
pwsh -NoProfile -File .\scripts\start.ps1 -OpenBrowser
```

独立语音服务：

```powershell
pwsh -NoProfile -File .\scripts\start-asr.ps1
pwsh -NoProfile -File .\scripts\start-tts.ps1
```

Web 界面默认位于 <http://127.0.0.1:8765/>，OpenAPI 位于 <http://127.0.0.1:8765/api/docs>。

## 配置和密钥

环境变量示例位于 [config/.env.example](config/.env.example)。也可以在产品设置界面配置 OpenAI-compatible LLM、SiliconFlow TTS、本地语音和只读能力。

请勿提交：

- `MINDSPACE_LLM_API_KEY`；
- `MINDSPACE_TTS_SILICONFLOW_API_KEY`；
- `runtime/update-keys/private.pem`；
- `runtime/config/settings.json`；
- 任何真实用户档案、会话或声音素材。

公开接口只返回脱敏配置；Prompt Inspector 默认同样脱敏。

## 模型和语音边界

- 中文向量、ASR/VAD/标点、CosyVoice 和 GPT-SoVITS 权重均按需安装，不进入源码仓库。
- `vendor/CosyVoice` 固定为上游 Git 子模块；`vendor/GPT-SoVITS` 是构建所需的代码快照。
- 角色权重、克隆声音、参考音频及生成音频可能具有额外授权要求，不属于 Mindspace 源码许可范围。
- 情绪推断运行链路在 0.5.7 中保持关闭，接口位置见 [EMOTION_INTERFACE.md](docs/EMOTION_INTERFACE.md)。

第三方来源和许可证边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 测试

后端：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
```

前端：

```powershell
npm --prefix frontend run check
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Launcher：

```powershell
npm --prefix desktop run check
npm --prefix desktop test
```

综合验证：

```powershell
pwsh -NoProfile -File .\scripts\verify.ps1
pwsh -NoProfile -File .\scripts\verify-source-integrity.ps1
```

## 打包

生成 Core 更新包：

```powershell
pwsh -NoProfile -File .\scripts\build-update.ps1 -Version 0.5.13
```

生成 Electron Launcher：

```powershell
npm --prefix desktop run package:app
```

详细的离线运行时、更新签名、安装包和回滚规则见 [PACKAGING.md](docs/PACKAGING.md) 与 [ONLINE_UPDATE_RELEASE.md](docs/ONLINE_UPDATE_RELEASE.md)。

## 文档索引

- [产品与首次使用](docs/PRODUCT_INTRODUCTION.md)
- [产品架构](docs/PRODUCT_ARCHITECTURE.md)
- [完整调用链](docs/APPLICATION_FULL_CHAIN.md)
- [代码精读指南](docs/CODE_READING_GUIDE.md)
- [工程师手册](docs/ENGINEER_HANDBOOK.md)
- [记忆、RAG 与 Prompt](docs/DEVELOPER_MEMORY_RAG_PROMPT.md)
- [JSON 档案与记忆](docs/structured-json-memory.md)
- [模型输入和 JSON 编排](docs/LLM_JSON_ORCHESTRATION.md)
- [七项成熟化改造](docs/MATURITY_HARDENING.md)
- [ASR 最终复核与仲裁](docs/ASR_FINAL_REFINEMENT.md)
- [语音通话与面对面互动](docs/VOICE_INTERACTION_MODES.md)
- [运行手册](docs/RUNTIME_RUNBOOK.md)
- [验证手册](docs/VERIFICATION.md)

## 许可说明

仓库公开并不自动授予模型权重、角色声音、参考音频或第三方项目的再分发权。Mindspace 原创代码的统一许可证应以仓库根目录未来发布的 `LICENSE` 为准；在许可证明确前，请勿将源码公开可读误解为获得商业或再分发授权。
