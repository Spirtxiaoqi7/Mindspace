> 文档状态：historical。仅保留历史证据，不得作为当前操作说明；当前权威见 `docs/INDEX.md`。

# Mindspace 0.7.0 本地正式发布验证

验证日期：2026-07-30

Core / Web：0.7.0

Launcher：0.5.52

## 发布结论

0.7.0 已作为完整桌面应用进入本机真实工作区。真实 Core 位于
`A:\Mindspace\application\core`，桌面程序位于
`A:\Mindspace\desktop-app`，应用与数据根目录保持为 `A:\Mindspace`。

官网热更新目录尚未发布。本文件记录的是本地正式构建和真实环境验证，
不能作为线上 Catalog 已切换的证明。

## 产物校验

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `Mindspace-0.5.52-x64.exe` | 120693141 | `5431fb0cc9a684ede65fa78a9c006ab24a2666a78c4d82ba4ba5ecc6e5f6d354` |
| `mindspace-core-0.7.0.zip` | 10850717 | `d4dfb3f42df6b5da0013f1d9e0814e4f487da12869da8e9d9af3062f4559537d` |

安装器当前没有 Windows Authenticode 证书签名，状态为 `NotSigned`。
Core 更新包使用项目现有 Ed25519 更新签名；本地测试 Manifest 的 URL 指向
回环测试服务，不得原样发布到官网。

## 自动验证

- 后端全量 pytest 通过。
- 前端 68 项测试、TypeScript 检查和生产构建通过。
- 桌面端 73 项测试、TypeScript 检查和 Electron 构建通过。
- 更新器完成安装、健康检查和回滚闭环；0.7.0 新文档已加入构建端与安装端
  同一允许目标清单，并有漂移回归测试。
- 零环境测试在污染 `PYTHONHOME`、`PYTHONPATH`、Conda 和 venv 变量的条件下通过：
  私有 Python、Core 0.7.0、向量模型和前端均由安装器独立准备。
- 隔离安装器 QA 通过全新安装、运行中覆盖、关闭旧进程、保留用户数据和卸载保数。
- 真实 0.6.0 数据副本迁移后，角色、活动、日记和共同片段接口均返回 200。

## 真实工作区验证

- 真实 Core 从 0.6.0 原子升级到 0.7.0。
- `/api/v1/health` 返回 0.7.0，且 `runtime_dir` 为 `A:\Mindspace\data`。
- 真实数据加载 2 张角色卡和 3 套活动；原有角色、头像、会话和迁移片段可见。
- 启动器显示 Core 0.7.0 运行中，数据目录为 `A:\Mindspace`。
- 未启用 ASR 时，进入应用前明确提示本次仅文字；Core 和聊天不等待语音服务。
- 主应用成功进入 0.7.0 模式大厅，显示“灵感抽卡”“自定义模式”和 2 张典藏卡。

## 回滚

- 升级前完整回滚点：
  `A:\Mindspace\backups\pre-0.7.0-20260730-142620`
- Core 原子回滚令牌：
  `0.7.0-1785394041-2b6d9237`
- 更新前版本：0.6.0

回滚时优先使用更新器令牌；只有更新器无法执行时才使用完整目录备份。
不得删除或覆盖 `A:\Mindspace\data`、`models`、`environment` 和 `user-data`。
