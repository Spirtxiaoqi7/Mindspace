# 前端参考与取舍

本次调研只借鉴公开产品原则，没有复制仓库代码或视觉资产。

## assistant-ui

仓库：[assistant-ui/assistant-ui](https://github.com/assistant-ui/assistant-ui)

采用的原则：

- 把 Thread、Message、Composer、ActionBar 视为独立交互边界。
- 输入器是主操作区域，发送、停止和语音必须在同一视线范围。
- 保留自动滚动、重试、键盘快捷键和可访问名称。
- LangGraph 进度应映射为前端可理解的运行时状态。

## Open WebUI

仓库：[open-webui/open-webui](https://github.com/open-webui/open-webui)

采用的原则：

- 语音打断必须随时可达，不能藏在设置中。
- TTS 按句/按回复消费，播放和生成取消要统一。
- 麦克风录音启用回声消除和噪声抑制。
- 明确显示服务是否在线及降级路径。

## LobeHub

仓库：[lobehub/lobehub](https://github.com/lobehub/lobehub)

采用的原则：

- TTS/STT 是会话能力而不是独立工具页面。
- 会话、知识、模型和服务状态保持清晰层级。
- 桌面与移动端共享同一核心交互。
- 结构化、可编辑记忆优先于不可见的全局记忆。

## HuggingChat Chat UI

仓库：[huggingface/chat-ui](https://github.com/huggingface/chat-ui)

采用的原则：

- 后端尽量收敛到兼容协议，前端不绑定模型供应商。
- 构建和容器化是产品能力的一部分。
- 对话历史、模型连接和错误状态需要可恢复。

## 本项目的具体取舍

- 使用原生 HTML/CSS/JS，减少 Node 构建链和便携包体积。
- 使用 CSS 变量和语义化组件边界，后续可迁移到 React/Svelte 而不改变 API。
- 视觉采用低饱和深色工作区、单一薄荷绿状态色，避免大面积渐变和模板化卡片堆叠。
- 不显示模型的隐藏分析；只显示用户可理解的节点阶段和召回数量。
- 语音默认浏览器降级，服务端模型按配置渐进增强。

