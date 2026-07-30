# 语音会话架构（0.5.39）

Mindspace 的桌面语音链路由一个 `VoiceIntent` 约束：`intent_id`、`generation` 和
`event_seq` 必须同时匹配，前端才会接收 ASR 事件。这样关闭、重连或打断留下的旧事件
不能影响下一次对话。

```text
常驻 ASR Worker
  └─ Windows 原生麦克风（仅内存 PCM，不落盘）
       └─ Core WebSocket relay
            └─ 前端 VoiceIntent
                 ├─ ASR 仲裁 → LLM
                 └─ 单请求 TTS 队列 → Worklet 播放
```

- 原生采集优先 WASAPI 的设备原生采样率，使用本地轻量转采样交给 16 kHz ASR；上次成功的
  端点会写入运行目录 `state/voice-capture-endpoint.json`。
- 退出通话只会停掉当前识别会话、LLM 输出和播放，不会关闭 ASR/TTS 服务进程；没有语音订阅时
  输入帧被直接丢弃。
- 原生首帧暂不可用时，界面显示“正在准备麦克风”。浏览器 `getUserMedia` 仅在用户点击
  “切换备用采集”后使用，避免两个组件同时占用同一个 USB 设备。
- 本地 TTS 一次只把一个片段送进 Qwen3-vLLM、GPT-SoVITS 或 CosyVoice。Qwen3 使用
  8091 的原始 PCM 流，首个 PCM 到达 Worklet 即可播放；首句优先，后文按大段累计。
- Qwen3 与 ASR 没有启动依赖。Launcher 只在用户选中 Qwen3 时管理其 WSL2 进程、后台 warm-up
  和健康检查；WSL2、WSL GPU、至少 14 GB 显存、模型脚本或端口条件不满足时不开放安装。
  切换 GPT-SoVITS/Qwen 是单一事务：只停止受管旧进程、等待释放后才启动新引擎，禁止自动双开。
- 取消当前 LLM 或 TTS 只撤销该 `VoiceIntent` 的 HTTP 流、播放节点和排队段；ASR 保持订阅。Core
  重启造成的 `run.interrupted` 会保留文字部分、清掉旧音频并把通话恢复到“正在监听”。
- 语音轮正文可有一个前置 `[[voice:...]]` 标签。Core 在流式出口剥离标签并把有限语气映射为
  Qwen `instructions`；可见消息、会话、RAG 和长期记忆只有已去标签的正文。
- `GET /api/v1/audio/diagnostics` 只提供端点和队列健康数据，不提供 PCM、转写或任何密钥。
