# Mindspace 0.6.0 发布验证记录

## 发布范围

- Core / Web：`0.6.0`
- Launcher：保持 `0.5.52`
- 发布类型：签名 Core 热更新
- 不包含：Launcher 安装包、Python 运行时、WSL、vLLM、ASR/TTS 模型权重、用户数据、私人角色卡和发布私钥

## 产品变更

- 新增模式大厅、灵感抽卡工坊和典藏卡册。
- 新增多角色档案、头像、会话、运行状态、记忆和历史对话召回隔离。
- 新增 `.mindspace-card` 导入、导出、复制、归档和版本恢复。
- 抽卡最多调用一次 LLM；失败后使用本地合法模板，不进行第二轮协议修复。
- 旧单角色数据通过事务迁移为“原有角色”，迁移失败完整回滚。

## 美术验收

- 四张安装包占位头像均为原创二次元风格。
- 模式卡框、角色状态、图标和底纹采用统一的典藏卡册视觉语言。
- 禁止将早期真人写实图、用户头像或私人角色卡作为公共示例资源。
- 前端生产路由 `/assets/characters/placeholder-1.webp` 已在隔离 Core 中返回 HTTP 200。
- 美术 Manifest 共 42 项，安装资源小于 15 MB。

## 自动验证

- 后端全量测试：通过。
- 前端测试：4 个测试文件、67 个测试通过。
- 前端 TypeScript 检查与生产构建：通过。
- 桌面端测试：71 个测试通过。
- 桌面端 TypeScript / 构建检查：通过。
- 隔离 Core 包导入与 API 冒烟：通过。
- Beta 更新器检查、下载、安装和回滚：通过本地签名 Feed 验证。
- 私人角色标识公开源码扫描：无命中。

## 候选发布物

- 文件：`runtime/release-site/mindspace/core/releases/0.6.0/mindspace-core-0.6.0.zip`
- 大小：`8,825,632` 字节
- SHA-256：`288a25615215a3b3455b53e7822a6823576da78c8c95a404ba531eeae36b0148`
- Beta Catalog Sequence：`7`
- Beta Rollout：`100%`

候选包、Core Manifest 和 Beta Catalog 的文件大小与 SHA-256 已一致。

## 在线状态

截至 2026-07-30，本地候选包已经完成验证，但 Beta Catalog 尚未原子发布。公网 Beta 地址仍返回网站 HTML，不能视为有效更新源。

发布顺序保持：

1. 上传 Beta staging。
2. 验证 staging 文件大小、SHA-256、JSON MIME、签名和 Range。
3. 原子发布 Beta Catalog。
4. 使用真实 Launcher 完成一次公网更新与回滚验收。
5. 复用完全相同的 Core ZIP，依次发布 Stable `10% → 50% → 100%`，每一阶段使用严格递增 Sequence。

任何阶段失败均不得替换线上 Catalog。

## 桌面安装器封装

- Launcher 产品版本：`0.5.52`
- 内置 Core / Web：`0.6.0`
- 安装器：`Mindspace-0.5.52-x64.exe`
- 大小：`118,701,576` 字节
- SHA-256：`504602e34c3e37c6da68fc1e96e217c3af40ee44f82e2d036f24c50d9ed66d3c`
- Authenticode：`NotSigned`

隔离安装验证结果：

- 全新安装：`3.844` 秒。
- 首次启动成功展开 Core `0.6.0`。
- 运行中覆盖安装：`4.913` 秒，并关闭旧程序进程。
- 覆盖安装后用户数据保留。
- 卸载：`0.705` 秒，仅移除应用程序。
- 卸载后用户工作区和哨兵数据保留。
- 首次引导在 1180×760 截图中无溢出或遮挡。

安装器没有商业 Authenticode 证书签名。它可用于本机和内部测试，但在面向普通用户正式分发前，应配置受信任的 Windows 代码签名证书，避免 SmartScreen 警告。

## 零环境实机验收

使用安装器首次启动所展开的 Core，在全新隔离工作区执行真实基础环境安装。测试进程预先注入无效的 `PYTHONHOME`、`PYTHONPATH`、`VIRTUAL_ENV` 和 `CONDA_PREFIX`，验证 Launcher 不会继承宿主 Python/Conda 状态。

结果：

- PowerShell、MinGit、uv、CPython、Core venv 全部安装并通过探针。
- 国内控制入口均正确重定向至权威上游。
- 三个静态运行时支持 HTTP Range 和断点续传。
- Core 私有环境安装耗时：`114.321` 秒。
- 中文向量模型下载与校验耗时：`58.052` 秒。
- 零环境总准备时间约：`176.190` 秒。
- 私有环境占用：`2,370,036,657` 字节。
- 中文向量模型占用：`409,275,645` 字节。
- 下载缓存占用：`183,153,894` 字节。
- Core `0.6.0` 健康检查、首页和角色 API 均返回成功。
- 没有继承宿主 Python/Conda 污染。
- 没有安装或启动 ASR、TTS、WSL、vLLM，文字模式不被语音阻塞。

测试中发现并修复了 Windows Defender/索引器短暂占用 `uv.exe` 时目录原子提升返回 `EPERM` 的问题。最终版本对可恢复的 `EPERM / EBUSY / EACCES` 使用有限退避，并在持续占用时采用同卷安全复制回退；只有完整探针通过后才写入就绪标记，不会无限重试或产生假就绪。
