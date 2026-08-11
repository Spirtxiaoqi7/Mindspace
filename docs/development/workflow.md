---
status: current
scope: Mindspace development workflow
last_reviewed: 2026-08-11
---

# 开发工作流 / Development Workflow

## 中文

### 工作边界

- `A:\RAG\Mindspace-admin` 是唯一可编辑源码；所有开发、审阅和提交均在此目录进行。
- `A:\Mindspace` 是运行目录，不是开发副本。源码构建、脚本和排障不得覆盖其中的应用、环境、模型、日志或用户数据。
- 不提交生成包、bootstrap 输出、报告、真实 API 原始日志、用户数据或临时视觉资产。

### 变更流程

1. 每个分支和提交只承担一个可审阅职责；跨三个以上功能模块时，说明契约变化、原因和回滚边界。
2. 先更新或确认边界契约，再实现；业务 schema、数据库或 V2 扩展必须附迁移说明、回滚条件和旧数据测试。
3. 修改 FastAPI 路由、请求模型或版本信息后，更新 `contracts/openapi/mindspace.openapi.json`，并保持其与 `create_app()` 输出一致。
4. 产品版本只能从 `config/version.json` 派生；使用 `scripts/sync-version.mjs` 同步，并通过一致性检查确认。不得手工伪造受管生成文件。
5. `desktop/bootstrap/manifest.json` 只由正式打包时的 `desktop/prepare-bootstrap.cjs` 生成；运行时清单变更必须重新签名。

### 文档维护

- current 文档必须短、可执行，并在顶部保留 status、scope 和 last_reviewed。
- 旧说明由 Git 历史保留；当前权威入口统一由 `docs/README.md` 提供，不得把历史内容当作操作指令。
- 行为、门禁或权威来源变化时，必须在同一变更中更新相应 current 文档。

## English

### Working boundaries

- `A:\RAG\Mindspace-admin` is the only editable source; all development, review, and commits take place there.
- `A:\Mindspace` is a runtime directory, not a development checkout. Source builds, scripts, and diagnostics must not overwrite its application, environment, models, logs, or user data.
- Do not commit generated packages, bootstrap output, reports, raw real-API logs, user data, or temporary visual assets.

### Change flow

1. Give each branch and commit one reviewable responsibility; for changes spanning more than three feature modules, state contract changes, rationale, and rollback boundaries.
2. Update or confirm boundary contracts before implementation; business schema, database, or V2 extensions require migration notes, rollback conditions, and legacy-data tests.
3. After changing FastAPI routes, request models, or version information, update `contracts/openapi/mindspace.openapi.json` and keep it aligned with `create_app()` output.
4. Derive the product version only from `config/version.json`; synchronize with `scripts/sync-version.mjs` and confirm with the consistency check. Do not hand-forge managed generated files.
5. `desktop/bootstrap/manifest.json` is generated only by `desktop/prepare-bootstrap.cjs` during a formal package build; runtime-manifest changes require re-signing.

### Documentation maintenance

- Current documents must be short and actionable, with status, scope, and last_reviewed at the top.
- Preserve obsolete guidance through Git history; use `docs/README.md` as the single current authority and never treat historical content as operational instruction.
- When behavior, gates, or the source of authority changes, update the relevant current document in the same change.
