# 中文“大姐姐”音色候选

## 当前试听推荐：丽莎中文原声（仅限本机评估）

- 原始候选：`source_zh_genshin_lisa.wav`
- 克隆参考：`recommended_zh_genshin_lisa_clone.wav`
- CosyVoice 克隆试听：`preview_zh_genshin_lisa_clone.wav`
- 参考文本：`嗯，累了就歇一会儿吧。看你总是那么忙碌，还挺心疼的。`
- 风格：成熟、慵懒、亲昵，成年“大姐姐”感明显。
- 音频：6.97 秒；克隆版为 16 kHz、单声道 PCM16，已去直流、限峰并处理首尾淡入淡出。
- 克隆验证：`累了就休息一会儿吧，今天我会好好陪着你。` 的首句经 FunASR 回读为 `累了，就休息一会儿吧。`
- 来源：AI-Hobbyist `Genshin_Dataset` 的《原神》中文角色包；原始数据来自游戏客户端解包。
- 权利限制：原始角色声音及台词版权归 COGNOSPHERE；不得作为本项目可商用或可再分发的默认资产。

数据集说明：https://github.com/AI-Hobbyist/Genshin_Datasets

## 可发布备选：Serena 中文原生

- 克隆参考：`recommended_zh_serena_mature_clone.wav`
- CosyVoice 克隆试听：`preview_recommended_zh_serena_mature_clone.wav`
- 参考文本：`晚上好，今天辛苦了。先坐下来休息一会儿吧，我会一直在这里陪着你。`
- 风格：中文母语、成熟温柔、舒缓、有包容感，不幼态。
- 来源：Qwen/Qwen3-TTS 官方公开 Demo 的中文原生 Serena 音色。
- 模型许可：Apache-2.0。
- 音频：6.30 秒、16 kHz、单声道 PCM16，已校准响度与首尾。
- 克隆验证：`晚上好。今天辛苦了，过来让我抱抱你吧。` 经 ASR 完整回读。

官方来源：https://github.com/QwenLM/Qwen3-TTS

## 日文跨语言备选

来源：Akjava/QWEN3-TTS-Voice-Design-100-Japanese-Female-Designed-Voices  
许可：Apache-2.0  
原始地址：https://huggingface.co/datasets/Akjava/QWEN3-TTS-Voice-Design-100-Japanese-Female-Designed-Voices

这些音频由 Qwen3-TTS VoiceDesign 合成，不对应现实声优或现有动漫角色。

## 推荐顺序

1. `selected_046_a_gentle_female_voice_that_feels_like_speaking_softly_to_you.wav`
   - 风格：温柔、贴近、陪伴感，最像恋爱向二次元大姐姐。
   - 参考文本：`優しく語りかけるような女性の声。こんにちは。私の声はどうですか？また会いに来てね。`
   - 中文预览：`preview_046.wav`
2. `selected_041_a_calm_low-pitched_female_voice_with_subtle_sensuality.wav`
   - 风格：低沉、冷静、略带成熟感。
   - 参考文本：`落ち着いた低音で色気を帯びた女性の声。こんにちは。私の声はどうですか？また会いに来てね。`
   - 中文预览：`preview_041.wav`
3. `selected_001_a_low_calm_mature_female_voice.wav`
   - 风格：稳重、低缓、年上感最强，但本次中文测试首词有误读。
   - 参考文本：`低めで落ち着いた、大人っぽい女性の声。こんにちは。私の声はどうですか？また会いに来てね。`
   - 中文预览：`preview_001.wav`

中文预览统一文本：`晚上好。累了就过来坐一会儿吧，姐姐会陪着你的。`
