# 第三方组件说明

Mindspace 会调用或携带若干第三方开源组件。第三方代码继续受各自许可证约束，模型权重、角色音色、参考音频和生成内容不因本仓库公开而自动获得再分发授权。

## CosyVoice

- 上游：<https://github.com/FunAudioLLM/CosyVoice>
- 仓库方式：`vendor/CosyVoice` Git 子模块
- 当前固定提交：`074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`
- 许可证：以子模块内 `LICENSE` 及其第三方目录声明为准

## GPT-SoVITS

- 上游：<https://github.com/RVC-Boss/GPT-SoVITS>
- 仓库方式：`vendor/GPT-SoVITS` 构建代码快照
- 许可证：MIT，见 `vendor/GPT-SoVITS/LICENSE`

仓库只保留 Mindspace 运行和打包所需的代码，不包含 GPT-SoVITS 角色权重。

## 模型与声音素材

以下内容不进入 Git 仓库：

- FunASR、VAD、标点和中文向量模型；
- CosyVoice、GPT-SoVITS 模型权重；
- 角色音色、声音克隆权重、参考音频和候选试听；
- 用户上传或由应用生成的任何音频。

使用者需要自行确认模型、声音和生成内容在所在地区及使用场景下的授权与合规要求。
