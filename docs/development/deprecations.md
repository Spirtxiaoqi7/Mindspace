---
status: current
scope: Mindspace deprecation governance
last_reviewed: 2026-08-11
---

# 废弃治理 / Deprecation Governance

## 中文

### 基本原则

- 本文件是当前废弃对象、计划版本和删除门禁的权威来源。
- 登记废弃不等于立即删除；名称旧不等于没有调用。
- `<repo>` 是唯一可修改源码；不得为清理工作覆盖 `<home>` 或其中的用户数据。

### 废弃流程

1. 登记对象、当前调用证据、替代物、删除门禁和目标版本。
2. 迁移所有已知调用方，并补齐替代契约、数据迁移和旧数据测试。
3. 保留兼容层至少一个发布周期；以代码搜索、运行审计和契约测试证明零调用。
4. 通过删除门禁后移除实现、测试和 current 操作说明；保留最小 historical 记录。
5. 在同一变更中更新本文件、替代文档和版本计划。

### 特别门禁

- 路由或接口：保留契约测试，并确认前端、桌面、Launcher 和导入迁移均已迁出。
- 端口或兼容监听：证明 Launcher、preload、Core、ASR/TTS 和更新路径均不再引用；不得仅因端口名称旧而删除。
- 生成资产：只删除可再生且有当前生成者的输出；不得手改或预置 bootstrap manifest。

## English

### Core principles

- This document is the authoritative source for current deprecated items, planned versions, and removal gates.
- Registration does not mean immediate deletion; an old name does not prove zero usage.
- `<repo>` is the only modifiable source; cleanup work must not overwrite `<home>` or its user data.

### Deprecation process

1. Register the object, current call evidence, replacement, removal gates, and target version.
2. Migrate all known callers and add replacement contracts, data migration, and legacy-data tests.
3. Retain the compatibility layer for at least one release cycle; prove zero calls through code search, runtime audit, and contract tests.
4. After removal gates pass, remove implementation, tests, and current operational guidance; retain a minimal historical record.
5. Update this document, replacement documentation, and the version plan in the same change.

### Special gates

- Routes or interfaces: retain contract tests and confirm frontend, desktop, Launcher, and import migration have moved away.
- Ports or compatibility listeners: prove that Launcher, preload, Core, ASR/TTS, and update paths no longer reference them; do not remove them merely because the name is old.
- Generated assets: remove only reproducible output with a current generator; do not hand-edit or preseed a bootstrap manifest.
