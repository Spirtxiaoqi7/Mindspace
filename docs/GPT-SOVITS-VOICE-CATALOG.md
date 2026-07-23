# GPT-SoVITS 人物音色目录

本目录面向 Mindspace 启动器的人物音色下载器。界面仅显示“作品分类”和“人物音色”两个下拉框，不在主页平铺全部角色。

## 版本核验结论

核验方式不是依据压缩包名称猜测版本，而是通过 HTTP Range 读取远端 ZIP 中央目录，检查模型权重所在的内部根目录：`v4/` 记为 V4，`v2ProPlus/` 记为 V2ProPlus。同时记录远端文件的 revision、大小和 SHA-256。

- V4，共 38 个：
  - 原神：八重神子、雷电将军、丽莎、夜兰、申鹤、闲云、芙宁娜、阿蕾奇诺、玛薇卡、甘雨、凝光、北斗。
  - 鸣潮：长离、守岸人、吟霖、坎特蕾拉、今汐、椿、弗洛洛、珂莱塔、菲比、赞妮。
  - 绝区零：伊芙琳、丽娜、简、雅、朱鸢、耀嘉音、薇薇安、柳。
  - 崩坏三：爱莉希雅、妖精爱莉、伊甸、梅比乌斯、阿波尼亚、丽塔、姬子、八重樱。
- V2ProPlus，共 10 个：
  - 崩铁：卡芙卡、姬子、黑天鹅、黄泉、镜流、阮梅、翡翠、大黑塔、花火、知更鸟。

崩铁这 10 个远端归档内部均为 `v2ProPlus/`，启动器不会将它们误标为 V4。

## 2026 爱莉希雅

爱莉希雅使用 2026 年 V4 SoVITS LoRA，并复用公共 `s1v3.ckpt`，避免重复下载一份 GPT 权重。LoRA 包自身不含参考音频，因此安装器会从已核验的爱莉希雅 V4 完整归档中抽取默认参考音频。

- 来源：[AyerElysia/elysia-gpt-sovits-lora-v4](https://huggingface.co/AyerElysia/elysia-gpt-sovits-lora-v4)
- 本地种子：`A:\RAG\langgarph-rag\assets\models\tts\gpt-sovits\runtime\archives\v4-elysia-2026-lora.tar.gz`
- 大小：69,659,823 bytes
- SHA-256：`E1C20121C09961FDFDAA90DB050EB91AC061BDAC13F44C8FAB5EE16FCDC78472`

## 下载与安全约束

- 人物归档来源：[ModelScope GPT-SoVITS Model Collection](https://modelscope.cn/models/aihobbyist/GPT-SoVITS_Model_Collection)。
- 每个角色独立下载；公共基础模型与 CUDA 运行时只部署一次。
- 下载支持断点续传，并强制检查预期字节数与 SHA-256。
- 旧式中文 ZIP 文件名按 GBK 确定性解码，避免出现目录乱码和“压缩包结构不符合预期”。
- 解压拒绝越界路径和符号链接；参考音频按“默认优先、文件名排序”确定性选择。
- 下载完成不会自动切换音色，用户需另行点击“设为当前”。
- 第三方角色声音仅适合本地、非商业验证；公开分发或商业使用前必须另行确认角色、录音及模型授权。

机器可读的完整清单位于 `config/gpt-sovits-voices.json`，可复现核验脚本位于 `scripts/audit-gpt-sovits-voices.py`。
