# 情绪能力接口（暂时停用）

当前 0.5.7 优化版不加载或下载情绪模型，也不在对话图、ASR 流或后台线程中执行情绪分析。`assets/models/asr/SenseVoiceSmall` 已从源码资源与组件目录移除。

保留的稳定接入点：

- `src/mindspace_graph/ports.py` 中的 `EmotionPort`：核心工作流只依赖这个协议。
- `src/mindspace_graph/emotion_disabled.py` 中的 `DisabledEmotionCoordinator`：当前零开销实现。
- `src/mindspace_graph/emotion.py`：只保留可序列化的数据契约，不包含模型、音频分析、HTTP、线程池或持久化实现。
- `POST /emotion/results`：ASR Worker 保留兼容端点，当前固定返回 `enabled: false` 和空结果。
- 配置字段 `audio.emotion_enabled`：为旧配置兼容而保留，服务端会强制归一化为 `false`。

若以后恢复，新增实现应满足 `EmotionPort`，在 `build_container()` 中显式注入，并单独恢复图节点和模型组件。不得在模块导入时加载模型，不得阻塞可见回复，不得把情绪结果直接写入人物档案、长期记忆或检索库。
