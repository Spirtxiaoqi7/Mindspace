# 中文 ASR 整句复核与调度

> 有效输入、TTS 打断、低置信草稿和限频的当前规则见
> [MATURITY_HARDENING.md](MATURITY_HARDENING.md#6-asr-仲裁和限频)。

## 当前链路

Mindspace 保留 `paraformer-zh-streaming` 作为实时字幕、起声确认和打断依据。完整语句结束后，如果本地已安装 `Fun-ASR-Nano-2512`，ASR Worker 使用同一段 PCM 做一次整句复核，再将结果交给现有词表纠偏和前端合并逻辑。

未安装 Nano、模型加载失败、调度等待超时、音频过短或过长、TTS 播放回声风险时，系统直接保留 Paraformer 结果。复核失败不会关闭 WebSocket，也不会阻断后续语音。

## CUDA 调度

- Paraformer、FSMN-VAD、标点恢复和 Nano 共用一个进程内 CUDA 调度器。
- 所有模型调用串行执行；等待中的流式识别优先于整句复核。
- Nano 固定 `batch_size=1`、中文、ITN、最多 32 个热词和 192 个输出 token，不启用 vLLM、时间戳或说话人模型。
- Launcher 按 `API -> ASR -> 本地 TTS` 启动，并等待 ASR 模型加载完成后才启动 CosyVoice 或 GPT-SoVITS，避免两个进程并行搬运大模型。
- CUDA 使用延迟模块加载和可扩展显存段，降低共享 GPU 的碎片化风险。

## 动态断句

默认基础静音窗口为 600ms：

- 明确句末标点：400ms。
- `嗯、呃、那个、就是、然后、怎么说、我想想` 等犹豫尾词：900ms。
- 播放或插话场景：至少 850ms。
- 尚未产生首个文字：至少 650ms。

旧版保存的默认 250ms 会迁移到 600ms；用户主动设置的其他数值保持不变。前端多段合并窗口继续使用原有 350ms，不额外拉长普通语句。

## 配置与接口位置

- 会话、调度、Nano 加载与回退：`src/mindspace_graph/streaming_asr.py`
- 独立 ASR Worker：`src/mindspace_graph/asr_worker.py`
- 主服务到 Worker 的控制字段：`src/mindspace_graph/api.py`
- 产品默认值和迁移：`src/mindspace_graph/product_config.py`
- 启动与显存屏障：`desktop/main.cjs`、`desktop/service-policy.cjs`
- 可选模型组件：`desktop/component-manager.cjs`
- 本机基准脚本：`scripts/benchmark-asr-final.py`

最终事件保留 `stream_text`、`raw_text`、`text`、`correction_matches`、`endpoint_reason` 和 `refinement` 元数据，便于区分流式初稿、Nano 原稿、确定性纠偏结果以及回退原因。

## 5060 Ti 16GB 实测

使用项目自带 5.616 秒中文样例，原生 PyTorch、FunASR 1.3.15：

- Paraformer/VAD/标点加载约 6.9 秒。
- Nano 首次加载约 43.4 秒；启动时执行一次短预热。
- 预热后整句复核约 392ms，RTF 约 0.069。
- Paraformer 与 Nano 合计 CUDA reserved 约 3342MiB。

加载时间受磁盘缓存和运行时版本影响，交付验收应继续记录 p50/p95，而不能把单次样例当作性能承诺。
