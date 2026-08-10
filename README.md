# Mindspace 0.9.0

Mindspace 是本地优先的 AI 角色、长期会话与多模态桌面系统。0.9.0 将角色创建、聊天、记忆、工具调用和本地语音运行时收束为一条可维护的正式产品链。

> 当前稳定源码：`main` / `v0.9.0`
> 
> 如果你不想配置环境，我们也提供了exe版本一键下载，直接使用安装包，省去一切配置问题，同时功能齐全，官网：www.douyinqijun.cn
> 
> 唯一开发目录：`A:\RAG\Mindspace-admin`
>
> 桌面运行目录：`A:\Mindspace`，不是开发工作树；`A:\Mindspace\data` 永远不进入源码提交。

## 当前产品链

- **V7 命格画布**：从角色种子生成 8 个方向，96 张命签按可恢复的 `6 + 6` 双批完成；十二项选择后合成标准 `chara_card_v2` 并进入本地聊天。
- **V2 角色与简洁用户档案**：角色长期权威数据采用 V2 卡；用户档案只保留名字、性别和 500 字手动补充资料，长期事实统一交给记忆中心。
- **连续会话与三层记忆**：近期对话与压缩摘要维持短期一致性，六槽事件记忆承接中期事项，长期 RAG 和结构化记忆按需召回且按角色、会话隔离。
- **原生工具调用**：`web`、`memory`、`task` 进入 LangGraph 单工具链；工具结果以数据回注，失败不能伪装成已核实或已完成。
- **聊天交互重构**：互动标签、多选组合、场景、消息更多菜单、工具状态、物理时间与压缩详情统一进入当前聊天体验。
- **本地语音与环境复用**：ASR、CosyVoice、GPT-SoVITS、Qwen3-TTS 和 FFmpeg 支持有界发现、已有环境复用、失败重试和按需安装，不进行全盘扫描。

## 代码维护基线

- `config/version.json` 是产品版本唯一真源，版本消费者与生成资产由脚本同步。
- Core、Web、Launcher、模型、运行时和用户数据保持明确边界；发布代码不读取桌面明文密钥或私密对话。
- 当前文档、历史文档、原型和一次性报告在 [文档状态索引](docs/INDEX.md) 中分级，只有 `current` 文档可作为执行依据。
- 旧工具规划链、旧角色权威档案和旧用户字段不再参与当前产品链。
- 临时测试目录、真实 API 结果、模型、日志、安装包和用户数据不进入 Git。

## 开发与构建

```powershell
Set-Location A:\RAG\Mindspace-admin

node scripts\sync-version.mjs --check
node scripts\verify-version-consistency.mjs
node scripts\verify-repository-policy.mjs

npm --prefix frontend ci
npm --prefix frontend run build

pwsh -File scripts\build-update.ps1 -Version 0.9.0
npm --prefix desktop ci
npm --prefix desktop run dist
```

完整 Python、Web、Launcher 测试和真实 API 验收要求见 [验证门禁](docs/VERIFICATION.md)。真实 API 与成人内容回归必须使用隔离数据目录，不属于公开仓库或 CI 输入。

## 当前权威文档

- [完整调用链](docs/APPLICATION_FULL_CHAIN.md)
- [代码阅读指南](docs/CODE_READING_GUIDE.md)
- [功能与模块索引](docs/MINDSPACE_FUNCTION_MAP.md)
- [运行手册](docs/RUNTIME_RUNBOOK.md)
- [封装说明](docs/PACKAGING.md)
- [验证门禁](docs/VERIFICATION.md)
- [版本与生成资产](docs/VERSIONING_AND_GENERATED_ASSETS.md)
- [废弃清单](docs/DEPRECATION_REGISTER_0.9.0.md)
- [分支与提交规范](docs/DEVELOPMENT_WORKFLOW_0.9.0.md)

版本变化只在 [CHANGELOG](CHANGELOG.md) 与发布日志中展示，旧设计不代表当前运行链。
