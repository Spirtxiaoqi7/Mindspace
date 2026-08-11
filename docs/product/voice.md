---
status: current
scope: product voice interaction and local runtime behavior
last_reviewed: 2026-08-11
---

# Voice Interaction / 语音交互

## 中文

### 三种交互方式

Mindspace 的对话可以使用文字、语音识别和语音合成，它们可以组合使用，也可以独立使用：

- **文字聊天**始终是基础路径：输入文字、阅读回复，不依赖麦克风、本地模型或 GPU。
- **ASR（语音识别）**把麦克风中的语音转换为文字，再沿用正常的对话流程。实时结果可用于确认你是否正在说话；在可用时，完整语句还可以接受本地复核以提高结果质量。
- **TTS（语音合成）**把已经生成的文字回复朗读出来。它不会改变屏幕上的正文，也不要求用户必须开启麦克风。

语音是文字对话的增强，而不是另一套聊天系统。关闭语音后，已有会话和文字聊天仍可正常工作。

### 复用已有环境，按需启用

本地语音能力会先检查已有的环境、模型、硬件和端口条件。满足条件时，Mindspace 可以复用可用的本地组件；不满足条件时，会说明缺少什么，而不是假装已经可用。

需要额外模型或组件的功能采用按需安装：只有在用户选择使用相应能力后，才应显示明确的安装或下载入口。基础文字聊天、资料检索和角色资料不应因为用户没有安装 ASR、TTS 或某个声音模型而无法使用。不同语音引擎对 Windows、GPU、显存和本地运行环境的要求不同，界面应以实际检测结果为准。

### 失败时如何处理

语音链路的任何一段都应可恢复，且不会把失败伪装成成功：

- 麦克风不可用、识别服务未启动或识别质量不足时，提示用户改用文字输入或重新尝试；文字聊天继续可用。
- 可选的整句复核模型未安装、加载失败或等待超时时，保留实时识别结果，而不是中断后续语音。
- TTS 引擎未安装、启动失败、排队超时或崩溃时，保留已经生成的文字回复，并允许用户继续阅读和聊天。
- 切换本地 TTS 引擎时，应安全地停止由应用管理的旧引擎；无法安全切换时，明确说明原因，不自动同时占用多个语音引擎。

诊断信息应帮助用户判断端点、队列和健康状态，但不应暴露音频内容、转写内容或密钥。

### 给新开发者

- 把 ASR、对话生成和 TTS 设计成可独立启动、停止和降级的组件；不要让某一个语音组件成为文字聊天的前置条件。
- 语音输入应先变成正常文本请求，语音输出应只消费已经确定的可见回复，避免语音链路改写对话事实。
- 优先复用受支持的本地环境；安装、模型下载和引擎切换必须由用户发起，并报告真实的检查结果。
- 处理取消、重启和故障时，保留已确认的文字结果，清理过期音频任务，并让会话回到可继续输入的状态。

## English

### Three interaction paths

Mindspace can use text, speech recognition, and speech synthesis in a conversation. They can work together or independently:

- **Text chat** is always the base path: users type and read replies without requiring a microphone, local model, or GPU.
- **ASR (automatic speech recognition)** converts microphone speech into text and then uses the normal conversation flow. Live results can confirm that the user is speaking; when available, a completed utterance can also receive local refinement for better quality.
- **TTS (text-to-speech)** reads an already generated text reply aloud. It does not change the text shown on screen and does not require the user to enable a microphone.

Voice is an enhancement to text conversation, not a separate chat system. Turning voice off leaves existing sessions and text chat available.

### Reuse existing environments and enable on demand

Local voice features first check the available environment, models, hardware, and port conditions. When requirements are met, Mindspace can reuse supported local components. When they are not, it explains what is missing instead of presenting the feature as ready.

Features that need additional models or components use on-demand installation: a clear install or download path should appear only after the user chooses that capability. Base text chat, retrieval, and character data must remain usable when ASR, TTS, or a particular voice model is not installed. Voice engines have different Windows, GPU, VRAM, and local-runtime requirements, so the UI should follow the actual check result.

### What happens when something fails

Every part of the voice path should be recoverable, and a failure must not be presented as success:

- If a microphone is unavailable, recognition is not running, or recognition quality is insufficient, the user is prompted to type or retry; text chat remains available.
- If an optional full-utterance refinement model is absent, fails to load, or times out, the live recognition result is retained instead of stopping later speech interaction.
- If a TTS engine is absent, fails to start, times out in its queue, or crashes, the generated text reply remains available to read and continue the conversation.
- When changing local TTS engines, safely stop the old engine only when it is managed by the application. If switching is not safe, explain why rather than automatically occupying multiple engines at once.

Diagnostics should help users understand endpoint, queue, and health state, but must not expose audio, transcripts, or secrets.

### For new developers

- Design ASR, conversation generation, and TTS as components that can start, stop, and degrade independently. Never make a voice component a prerequisite for text chat.
- Convert voice input into a normal text request first, and let voice output consume only a confirmed visible reply. The voice path must not rewrite conversational facts.
- Prefer reuse of supported local environments. Installation, model download, and engine switching must be user initiated and report actual check results.
- On cancellation, restart, or failure, preserve confirmed text, clear stale audio work, and return the session to a state where the user can continue input.
