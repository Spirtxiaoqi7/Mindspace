# 更新发布与容量基线

## 当前更新边界

- Launcher 安装包：`dist-launcher\Mindspace-0.3.4-x64.exe`，约 105 MiB。
- 核心签名更新：`runtime\update-feed\mindspace-core-0.3.4.zip`。
- 核心更新只包含后端、静态前端、脚本和锁文件；不包含 `runtime`、用户数据、虚拟环境或任何模型。
- 上线默认使用 SiliconFlow TTS API，本地 CosyVoice 模型和 5055 Worker 均为可选项。
- Launcher 内置基础核心，首次启动会解包到当前用户的可写工作区，不再使用构建机路径。
- 必需模型默认从 ModelScope 下载并继承 Windows 系统代理；合计约 1.6 GB。本地 CosyVoice 约 9.75 GB，同样使用国内 ModelScope；其可选运行时复用 ASR 的 CUDA/PyTorch 环境，不会被“下载全部必需组件”自动选择。
- Launcher 每 6 小时自动检查已配置的 HTTPS 清单，也支持手动检查、下载、安装和回滚。

发布服务器至少上传 `manifest.json` 和清单引用的 ZIP。签名私钥只保留在离线发布环境的 `runtime\update-keys\private.pem`；服务器和安装包均不得包含私钥。

## 实测本机磁盘组成

| 组件 | 当前体积 |
|---|---:|
| Electron 解包目录 | 0.38 GiB |
| 主 Python 环境 | 0.91 GiB |
| ASR Python 环境 | 5.85 GiB |
| ASR/VAD/标点模型 | 1.11 GiB |
| 中文向量模型 | 1.43 GiB |
| 不含本地 TTS 的完整本地运行占用 | 9.67 GiB |
| 已排除的本地 TTS 模型 | 9.27 GiB |

因此客户端保留本地 ASR 与向量模型时应预留至少 12–15 GiB 磁盘。约 110.4 MB 的 EXE 当前是 Launcher 安装包，不代表一台新电脑所需的完整本地 AI 运行环境；基础核心随安装包提供，Python 环境与模型仍由 Launcher 引导下载。

## 静态更新服务器

- 仅保留当前安装包、5 个核心版本和回滚文件时，1 GiB 存储足够；建议使用 5–10 GiB 对象存储并启用 CDN。
- 1,000 次 Launcher 下载约产生 110.4 GB 下行流量。
- 1,000 个客户端下载一次 0.89 MB 核心更新约产生 0.89 GB 下行流量。
- 若以后托管当前完整本地运行时，单个平台单版本接近 10 GiB，保留 3 个版本至少需要 30 GiB；1,000 次完整首装可能达到约 10 TB，必须使用分包、增量和 CDN。

## 中央 API 部署参考

当前默认架构由每台客户端运行 FastAPI、ASR 和向量检索，LLM/TTS 直接使用外部 API。这种方式下，自有服务器只承担安装与更新流量。

如果将 FastAPI 集中部署：

- 无本地推理的小规模试运行可从 2 vCPU / 4 GiB RAM / 20–40 GiB SSD 起步。
- 约 10–30 个并发会话建议 4 vCPU / 8 GiB RAM；更大规模需要把文件 JSON 存储迁移为数据库/对象存储，并加入租户隔离、认证、限流和任务队列。
- 24 kHz、16-bit、单声道 PCM TTS 约为 48,000 bytes/s，即 0.384 Mbit/s 或 2.88 MB/min。20 路同时朗读约 7.7 Mbit/s，100 路约 38.4 Mbit/s，生产带宽至少保留两倍余量。
- 若每位日活用户每天朗读 10 分钟，TTS 音频约 0.864 GB/用户/月；1,000 日活约 864 GB/月。
- 若 ASR 也改为中央服务，16 kHz PCM 输入约 1.92 MB/min；同样每天 10 分钟时，1,000 日活约 576 GB/月。

LLM/TTS 供应商费用不在这些流量数字中，应按实际 token、音频时长和供应商计价单独核算。

## 发布前仍需外部条件

1. 正式 HTTPS 更新域名/CDN，并在 Launcher 中保存其 `manifest.json` 地址。
2. Windows 代码签名证书；Ed25519 只验证核心更新内容，不替代系统对 EXE/NSIS 的代码签名信任。
3. 新电脑首装时由 Launcher 下载 Python/ASR/向量模型；大规模分发建议为模型源和 Python 包配置国内 CDN 缓存。
4. 真实 LLM 与 SiliconFlow 密钥下运行一次统一自检和实时语音冒烟测试。
