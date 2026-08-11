> 状态：report。本文记录 0.9.0 模块化重构的一次性验收结果，不替代 current 架构文档或运行手册。

# Mindspace 0.9.0 模块化重构验收报告

验收日期：2026-08-11

验收对象：`A:\RAG\Mindspace-admin`

## 结论

本轮已完成前端、后端、存储、提示词与桌面进程的责任拆分，并建立可自动执行的依赖边界。最终全量测试、类型检查、生产构建、OpenAPI 合同检查和代码库索引生成均通过。

源码验收通过不等于桌面运行目录已经覆盖。本轮未提交 Git，也未部署到 `A:\Mindspace`，用户数据目录未被修改。

## 关键修复

- Profile 1.3 精简后残留的旧字段读取已移除；历史版本恢复恢复为成功响应。
- JSON Pointer 在中间节点缺失、数组索引非法或越界时稳定返回 `None`。
- 角色创建、编辑、恢复、导入与命格提交只重建当前角色记忆，避免全局旧数据导致 422/404。
- 检索预热由公开 `RetrievalWarmupCoordinator` 管理，并提供 `drain()` 生命周期接口，测试不再依赖私有任务字典。
- 前端 API、Modal、Field 和回合发送类型迁入 shared 层；功能模块之间不再通过聊天模块互相穿透。
- 桌面主进程的诊断、语音、Qwen、本地运行时、引导、存储和陪伴资源逻辑已拆入控制器。
- 过期测试已迁移到当前公开接口和 Profile 1.3 数据结构，没有恢复废弃字段。

## 自动化结果

| 范围 | 命令 | 结果 |
|---|---|---|
| Python 全套 | `uv run python -m pytest --basetemp .pytest-tmp-final2` | 364 passed，1 个外部弃用警告 |
| 后端依赖边界 | `uv run lint-imports` | 89 文件、238 依赖、8/8 契约通过 |
| OpenAPI 合同 | `uv run python scripts/export-api-contracts.py --check` | 快照为最新 |
| 前端依赖边界 | `node scripts/verify-frontend-boundaries.mjs` | 73 文件、196 本地依赖、0 违规 |
| 前端测试 | `npm test` | 12 文件、59/59 通过 |
| 前端类型检查 | `npm run check` | 通过 |
| 前端生产构建 | `npm run build` | 通过 |
| 桌面测试 | `npm test` | 113/113 通过 |
| 桌面检查 | `npm run check` | 通过 |
| 桌面生产构建 | `npm run build` | 通过 |
| 代码库索引 | `node scripts/generate-codebase-index.mjs` | 466 个维护文件 |

## 保留风险

- Starlette `TestClient` 仍报告 `httpx` 兼容层弃用警告；该警告来自当前测试依赖，不影响本轮功能结果，应在依赖升级任务中单独处理。
- 本轮没有调用真实外部模型 API。重构验收覆盖本地产品逻辑、接口合同和构建，不消耗用户 API 配额，也不构成线上模型行为验收。
- 本轮没有执行安装包封装、桌面覆盖或 Git 发布；这些步骤必须在用户明确要求后独立进行，并保护 `A:\Mindspace\data`。

## 维护入口

- 总体边界：`docs/MODULAR_ARCHITECTURE.md`
- 前端：`docs/ARCHITECTURE_FRONTEND.md`
- 后端：`docs/ARCHITECTURE_BACKEND.md`
- 存储：`docs/ARCHITECTURE_STORAGE.md`
- 提示词：`docs/ARCHITECTURE_PROMPTS.md`
- 桌面：`docs/ARCHITECTURE_DESKTOP.md`
- 分区索引：`docs/CODEBASE_INDEX_0.9.0.md`
- 逐文件索引：`docs/CODEBASE_FILE_INDEX_0.9.0.md`
