---
status: current
scope: Mindspace testing and verification
last_reviewed: 2026-08-11
---

# 测试与验证 / Testing and Verification

## 中文

### 安全边界

- 在 `A:\RAG\Mindspace-admin` 执行开发验证；不得把测试输出写入或覆盖 `A:\Mindspace`，尤其不得覆盖其中的用户数据。
- 自动门禁不访问真实 API、不读取用户数据、不执行正式打包。
- 真实 API、成人内容和私密对话只能隔离、本地且显式执行；原始输出留在忽略目录，只提交脱敏统计摘要。

### 测试层级

1. 治理：运行版本一致性、GPT-SoVITS catalog 漂移和仓库政策检查。
2. 后端：冻结依赖后运行 Ruff 与 `pytest`，覆盖服务、图、持久化和契约行为。
3. 前端：运行 `npm run check`、`npm test` 和 `npm run build`。
4. 桌面：运行 `npm test` 与 `npm run check`；Windows CI 还执行更新脚本的 `-DryRun`。
5. 手工验收：仅在需要证明真实 provider 行为时执行，并报告范围、脱敏结果与未验证项。

### 执行规则

- 按变更影响选择最小充分层级；合并前完成适用的治理、后端、前端和桌面门禁。
- Windows 脚本必须由 Windows job 验证。
- 失败不得被 fallback 或摘要掩盖；记录失败层级、错误类别和未覆盖风险。

## English

### Safety boundaries

- Run development verification in `A:\RAG\Mindspace-admin`; never write test output to or overwrite `A:\Mindspace`, especially its user data.
- Automated gates do not call real APIs, read user data, or perform formal packaging.
- Real APIs, adult content, and private conversations may run only locally, in isolation, and by explicit action; keep raw output in ignored directories and commit only redacted statistical summaries.

### Test layers

1. Governance: run version-consistency, GPT-SoVITS catalog-drift, and repository-policy checks.
2. Backend: after frozen dependency sync, run Ruff and `pytest` covering service, graph, persistence, and contract behavior.
3. Frontend: run `npm run check`, `npm test`, and `npm run build`.
4. Desktop: run `npm test` and `npm run check`; Windows CI also executes the update script with `-DryRun`.
5. Manual acceptance: run only when real-provider behavior must be demonstrated, and report scope, redacted results, and unverified items.

### Execution rules

- Select the smallest sufficient layers for the change; before merging, complete all applicable governance, backend, frontend, and desktop gates.
- Windows scripts must be verified by a Windows job.
- Failures must not be hidden by fallbacks or summaries; record the failed layer, error class, and remaining coverage risk.
