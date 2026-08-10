# Mindspace 0.8.3

Mindspace 是本地优先的 AI 角色、长期会话与多模态桌面系统。唯一开发源是 `A:\RAG\Mindspace-admin`；桌面安装目录与用户数据目录不是开发工作树。

## 当前产品链路

- 角色创建使用 V7 十二节点命格画布。种子生成 8 个方向，96 张命签在同一个可见阶段内按前 6 类、后 6 类两次调用完成；任一半失败只重试失败半批，双批通过后才能选签。
- 十二项选择完成后生成 `chara_card_v2`，确认入库后进入本地聊天；下一次创建从全新旅程开始。
- 聊天请求进入 durable run。每轮持久化 provider attempt、工具 attempt 和终态，刷新或重连不得把无工具轮次伪装成任务处理。
- 工具使用原生短指令握手；旧 planner/research/repair 多模型链不再是当前架构。
- 模型密钥只属于本地设置/进程边界。仓库、CI、发布包、报告和测试夹具不得包含真实密钥或完整私密正文。

## 开发与验证

```powershell
cd A:\RAG\Mindspace-admin
node scripts/verify-version-consistency.mjs
node scripts/sync-gpt-sovits-catalog.mjs --check
node scripts/verify-repository-policy.mjs
uv sync --frozen --extra dev
uv run pytest -q
cd frontend; npm ci; npm run check; npm test; npm run build
cd ..\desktop; npm ci; npm test; npm run check
```

真实 API 与成人内容回归只能由开发者在隔离数据目录手工执行，不属于自动 CI，也不是发布输入。详情见 [验证说明](docs/VERIFICATION.md) 与 [本地报告规范](docs/LOCAL_REPORT_POLICY.md)。

## 权威文档

- [文档状态索引](docs/INDEX.md)
- [完整调用链](docs/APPLICATION_FULL_CHAIN.md)
- [代码阅读指南](docs/CODE_READING_GUIDE.md)
- [运行手册](docs/RUNTIME_RUNBOOK.md)
- [版本与生成资产](docs/VERSIONING_AND_GENERATED_ASSETS.md)
- [废弃清单](docs/DEPRECATION_REGISTER_0.8.3.md)
- [开发分支与提交规范](docs/DEVELOPMENT_WORKFLOW_0.8.3.md)

## 版本边界

`config/version.json` 是版本契约唯一真源。产品版本为 `0.8.3`；签名运行时组件包有独立版本，仍由同一契约声明。运行 `node scripts/sync-version.mjs` 生成受管消费者，运行 `--check` 或 `verify-version-consistency.mjs` 只校验、不修改。
