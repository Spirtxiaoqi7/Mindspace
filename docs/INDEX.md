# Mindspace 文档状态索引

状态定义：`current` 为当前操作权威；`historical` 只保留历史证据；`prototype` 是未承诺设计；`report` 是一次性审查/验收记录。只有 current 文档可以作为执行说明。

## Current

`APPLICATION_FULL_CHAIN.md`、`CODE_READING_GUIDE.md`、`MINDSPACE_FUNCTION_MAP.md`、`ARCHITECTURE_FRONTEND.md`、`ARCHITECTURE_BACKEND.md`、`ARCHITECTURE_STORAGE.md`、`ARCHITECTURE_PROMPTS.md`、`ARCHITECTURE_DESKTOP.md`、`MODULAR_ARCHITECTURE.md`、`ONLINE_UPDATE_RELEASE.md`、`PACKAGING.md`、`READ_ONLY_CAPABILITIES.md`、`RUNTIME_RUNBOOK.md`、`VERIFICATION.md`、`VERSIONING_AND_GENERATED_ASSETS.md`、`LOCAL_REPORT_POLICY.md`、`DEVELOPMENT_WORKFLOW_0.9.0.md`、`DEPRECATION_REGISTER_0.9.0.md`。

## Architecture and maintenance navigation

- [Frontend architecture](ARCHITECTURE_FRONTEND.md)
- [Backend architecture](ARCHITECTURE_BACKEND.md)
- [Storage architecture](ARCHITECTURE_STORAGE.md)
- [Prompt architecture](ARCHITECTURE_PROMPTS.md)
- [Desktop architecture](ARCHITECTURE_DESKTOP.md)
- [Modular architecture and dependency boundaries](MODULAR_ARCHITECTURE.md)
- [Codebase architecture index](CODEBASE_INDEX_0.9.0.md)
- [Per-file maintenance index](CODEBASE_FILE_INDEX_0.9.0.md)

## Historical

`ARCHITECTURE.md`、`ART_PREVIEW_PROVENANCE_0.7.0.md`、`ASR_FINAL_REFINEMENT.md`、`DEVELOPER_MEMORY_RAG_PROMPT.md`、`DEVELOPMENT_DESIGN_HISTORY.md`、`ENGINEER_HANDBOOK.md`、`GPT-SOVITS-VOICE-CATALOG.md`、`MATURITY_HARDENING.md`、`MIGRATION_ROLLBACK_0.6.0.md`、`MINDSPACE_0.5.49_INSTALLATION_VERIFICATION.md`、`MINDSPACE_0.5.49_PRODUCT_DELIVERY_PLAN.md`、`RELEASE_VERIFICATION_0.6.0.md`、`RELEASE_VERIFICATION_0.7.0.md`、`SHARED_CHAPTERS_ARCHITECTURE.md`。

## Prototype

`APPLICATION_ALGORITHM_FOUNDATION.md`、`CHARACTER_ART_LIBRARY.md`、`CHARACTER_CARD_PACKAGE.md`、`EMOTION_INTERFACE.md`、`FRONTEND_REFERENCES.md`、`GENDER_IDENTITY.md`、`IMPLEMENTATION_PLAN.md`、`LAUNCHER_ONBOARDING.md`、`LIVE2D_COMPANION_RESOURCE_BUDGET.md`、`LLM_JSON_ORCHESTRATION.md`、`MULTI_CHARACTER_ARCHITECTURE.md`、`PRODUCT_ARCHITECTURE.md`、`PRODUCT_INTRODUCTION.md`、`QWEN3_TTS_RUNTIME.md`、`r18-style-library.md`、`roleplay-card-v2.md`、`structured-json-memory.md`、`UPDATE_AND_CAPACITY.md`、`VOICE_INTERACTION_MODES.md`、`voice-session-architecture.md`、`ZERO_ENVIRONMENT_RUNTIME.md`。

## Report

`MINDSPACE_0.8.3_CODE_AUDIT_STAGE_1.md`、`MODULAR_REFACTOR_VALIDATION_0.9.0.md`。

## Generated

[CODEBASE_INDEX_0.9.0.md](CODEBASE_INDEX_0.9.0.md)、[CODEBASE_FILE_INDEX_0.9.0.md](CODEBASE_FILE_INDEX_0.9.0.md) 和 `contracts/openapi/mindspace.openapi.json`。前两者由 `scripts/generate-codebase-index.mjs` 生成；OpenAPI 快照由 `scripts/export-api-contracts.py` 生成。生成物不得手工编辑。

新增文档必须先登记状态。Historical/prototype/report/generated 文档必须在文件顶部显示状态横幅，不能被根 README 当作当前 runbook 链接。
