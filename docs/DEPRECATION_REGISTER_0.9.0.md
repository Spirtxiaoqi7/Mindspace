# 0.9.0 废弃清单

> 状态：current。登记不等于立即删除；有调用证据的兼容模块必须先迁移。

| 对象 | 当前调用证据 | 替代物 | 删除前门禁 | 计划版本 |
|---|---|---|---|---|
| 410 `/api/v1/character-drafts*` | `src/mindspace_graph/api.py` 仍注册 410 tombstone；当前入口由 V7 测试覆盖 | V7 journey + V2 commit | 路由契约测试保留一周期；前端 `rg` 与运行审计零调用 | 0.9.0 |
| 410 `/api/v1/characters/fate-options`、`/options` | `src/mindspace_graph/api.py` 仍有兼容响应；历史客户端可能探测 | V7 archetypes/cards | 桌面最低版本门禁、前端与 Launcher 零调用证据 | 0.9.0 |
| planner/research/protocol-repair 旧链 | 当前图测试与 `READ_ONLY_CAPABILITIES.md` 只承认原生工具 attempt；旧名称仅应出现在 historical 文档 | 原生短工具指令 + durable tool attempt | 图拓扑测试证明零边；`rg` 在 current 文档/运行入口零命中 | 0.9.1 |
| 历史 profile 接口 | `src/mindspace_graph/api.py` 及角色迁移测试仍保留兼容风险，未证实可删 | V2 character/card API | frontend、导入迁移、desktop 调用均迁出并有旧角色迁移测试 | 0.9.x |
| scene/presentation 接口 | `src/mindspace_graph/api.py` 与现有前端场景/展示调用仍可能使用，明确不是死代码 | 当前会话场景/V2 字段 | 精确调用方清单、替代 schema、数据迁移测试全部完成 | 未排期 |
| `scripts/run_082_real_api_regression.py` | 旧人工命令入口；现为 fail-closed tombstone | 环境变量专用、隔离的维护型本地 harness | 发布文档零引用；新 harness 有秘密扫描 | 0.9.1 |
| `scripts/run_082_two_card_tool_benchmark.py` | 同上 | 同上 | 同上 | 0.9.1 |
| 旧 profile/scene/presentation 文档 | 只允许 historical/prototype 状态 | `docs/INDEX.md` 中 current 权威 | 当前 runbook 零旧接口操作指令 | 0.9.0 完成治理 |
| 生成 Web source map | 不应存在；生产构建显式关闭 | 无，使用本地开发 sourcemap | CI 扫描 `.map` 和 `sourcesContent` | 0.9.0 禁止 |
| `desktop/bootstrap/manifest.json` | `desktop/prepare-bootstrap.cjs` 是唯一生成者；版本校验允许缺席、存在时校验产品版本 | 正式打包生成流程 | Core zip 版本、字节数、SHA-256 一致 | 持续生成资产 |

旧兼容端口不因名称“旧”而直接删除。`config/service-ports.json` 和 desktop 端口测试是当前权威；任何硬编码端口或兼容监听删除前必须证明 Launcher、preload、Core、ASR/TTS 和更新路径均不再引用。
