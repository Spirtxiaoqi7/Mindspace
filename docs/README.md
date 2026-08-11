---
status: current
scope: documentation-navigation
last_reviewed: 2026-08-11
---

# Mindspace 文档导航 / Documentation Guide

## 中文

这里是 Mindspace 当前文档的唯一入口。`current` 文档可以指导开发与运行；历史设计、原型和一次性报告不能覆盖当前实现。

### 架构

- [架构总览](architecture/overview.md)
- [前端](architecture/frontend.md)
- [后端](architecture/backend.md)
- [存储与记忆](architecture/storage-memory.md)
- [提示词与工具](architecture/prompts-tools.md)
- [桌面与本地运行时](architecture/desktop-runtime.md)

### 开发

- [开发流程](development/workflow.md)
- [测试门禁](development/testing.md)
- [废弃与兼容](development/deprecations.md)

### 运维

- [桌面运行与回滚](operations/runtime.md)
- [封装](operations/packaging.md)
- [发布](operations/release.md)

### 产品

- [产品概览](product/overview.md)
- [角色与命格](product/characters-destiny.md)
- [记忆与上下文](product/memory-context.md)
- [语音](product/voice.md)

### 架构决策

- [ADR-0001：运行根目录](adr/0001-runtime-home.md)
- [ADR-0002：模块边界](adr/0002-modular-boundaries.md)
- [ADR-0003：V2 角色卡](adr/0003-character-card-v2.md)
- [ADR-0004：单工具协议](adr/0004-single-tool-protocol.md)

发布事实继续由 [CHANGELOG](../CHANGELOG.md) 和 [release-history.json](release-history.json) 管理。完整逐文件索引不进入 Git；需要时运行 `node scripts/generate-codebase-index.mjs` 在本地生成。

仓库首页展示图片的来源、隐私边界与复现方式见 [README 展示资源](readme/ASSETS.md)。

## English

This is the single entry point for current Mindspace documentation. Documents marked `current` may guide development and operation. Historical designs, prototypes, and one-off reports cannot override the current implementation.

### Architecture

- [Architecture overview](architecture/overview.md)
- [Frontend](architecture/frontend.md)
- [Backend](architecture/backend.md)
- [Storage and memory](architecture/storage-memory.md)
- [Prompts and tools](architecture/prompts-tools.md)
- [Desktop and local runtime](architecture/desktop-runtime.md)

### Development

- [Development workflow](development/workflow.md)
- [Testing gates](development/testing.md)
- [Deprecation and compatibility](development/deprecations.md)

### Operations

- [Desktop runtime and rollback](operations/runtime.md)
- [Packaging](operations/packaging.md)
- [Release](operations/release.md)

### Product

- [Product overview](product/overview.md)
- [Characters and Destiny](product/characters-destiny.md)
- [Memory and context](product/memory-context.md)
- [Voice](product/voice.md)

### Architecture decisions

- [ADR-0001: Runtime home](adr/0001-runtime-home.md)
- [ADR-0002: Modular boundaries](adr/0002-modular-boundaries.md)
- [ADR-0003: V2 character cards](adr/0003-character-card-v2.md)
- [ADR-0004: Single-tool protocol](adr/0004-single-tool-protocol.md)

Release facts remain authoritative in the [CHANGELOG](../CHANGELOG.md) and [release-history.json](release-history.json). The full per-file index is not tracked by Git; run `node scripts/generate-codebase-index.mjs` when a local copy is needed.

See [README presentation assets](readme/ASSETS.md) for image provenance, privacy boundaries, and reproduction steps.
