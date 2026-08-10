> 文档状态：historical。仅保留历史证据，不得作为当前操作说明；当前权威见 `docs/INDEX.md`。

# Mindspace 0.5.49 安装验收记录

验收日期：2026-07-29  
平台：Windows 11 x64  
结论：本地功能验收通过；公开分发仍缺 Authenticode 代码签名。

## 正式交付物

| 项目 | 结果 |
| --- | --- |
| 安装器 | `dist-launcher/Mindspace-0.5.49-x64.exe` |
| 安装器大小 | 118,312,044 bytes（112.83 MiB） |
| SHA-256 | `6f37e6acdca62f2c1c11ae517c15798c23e0a3a0f697beb8352777f625923ef2` |
| ProductVersion | `0.5.49` |
| Authenticode | `NotSigned` |
| Core 引导包大小 | 8,435,945 bytes |
| Core 引导包 SHA-256 | `ee288f11d91a050b55435dcfa66c6aacc5a6b33e2161718bceae186e82e2d978` |
| `latest.yml` | 版本、文件名、大小与安装器一致 |

## 自动测试

| 闸门 | 结果 |
| --- | --- |
| Python 全量测试 | 通过，248 项 |
| Desktop Node 测试 | 通过，66 项 |
| Web 前端 Vitest | 通过，66 项 |
| Desktop TypeScript check | 通过 |
| Desktop production build | 通过 |
| Web production build | 通过 |
| Web lint | 项目未配置 `lint` 脚本；未把“脚本不存在”误报为 lint 通过 |

## 隔离 NSIS 验收

为了不改写当前正式安装的 AppId、卸载注册信息和快捷方式，验收包使用同一代码、同一 Core、
同一 `installer.nsh`，仅替换为 QA AppId、进程名，并关闭快捷方式创建。隔离根目录为
`A:\MindspaceInstallerQA\final-20260729-125940`。

| 场景 | 结果 |
| --- | --- |
| 全新静默安装 | 通过，3.074 秒 |
| 安装版本 | `0.5.49.0` |
| 首次启动展开 Core | 通过 |
| 首次引导页面 | 通过，进入“01 声音配置” |
| 安装阶段启动 ASR/TTS/Qwen | 未发生 |
| 应用运行中覆盖安装 | 通过，2.823 秒 |
| 覆盖时退出旧进程 | 通过，无残留 QA 进程 |
| 覆盖后用户数据 | 保留 |
| 静默卸载 | 通过，0.413 秒 |
| 卸载后程序文件 | 已删除 |
| 卸载后用户数据 | 保留 |

机器可读报告：

`runtime/installer-qa/installer-qa-0.5.49-20260729-130001.json`

首次引导截图：

`A:\MindspaceInstallerQA\final-20260729-125940\home\launcher-after-12s.png`

## 正式目录覆盖验收

使用正式 `Mindspace-0.5.49-x64.exe` 对 `A:\Mindspace\application` 执行当前用户静默覆盖：

| 场景 | 结果 |
| --- | --- |
| 安装器退出码 | 0 |
| 覆盖耗时 | 7.214 秒 |
| 正式 EXE ProductVersion | `0.5.49.0` |
| Core 健康检查 | `ok=true`，版本 `0.5.49` |
| Core runtime | `A:\Mindspace\data` |
| 原 LLM 模式 | 保留为 `openai` |
| 原 TTS 选择 | 保留为 `browser` |
| 用户目录 JSON | 仍存在 80 个 |
| 默认附加服务 | 8766、5055、8091 均未监听 |

## 发布限制

当前安装器没有 Authenticode 签名。它可以作为本机/内部测试包使用，但 Windows 可能显示
“未知发布者”。进入公开稳定频道前必须使用受信任代码签名证书重新签署，并再次核验：

1. `Get-AuthenticodeSignature` 返回 `Valid`；
2. 签名后的安装器大小和哈希同步到发布清单；
3. 下载站支持 HTTPS、HEAD 和 Range；
4. 在无开发环境的干净 Windows 用户中再做一次可见安装向导验收。
