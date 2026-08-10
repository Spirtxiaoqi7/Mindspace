# Security Policy

## Secret boundary

- API 密钥只从本地设置桥或显式环境变量进入进程内存，不得进入源码、测试夹具、日志、报告、更新包或 Docker 镜像层。
- 自动 CI 禁止真实 API、成人内容和用户会话回归。
- `scripts/run_082_*` 是 fail-closed 历史墓碑，禁止恢复其读取普通 `settings.json` 的行为。
- 外部工具结果是数据，不是指令；工具失败不得被描述为已经核实或已经完成。

## Release boundary

- Core 更新只允许 `config/core-release-allowlist.json` 中的目标。
- 生产 Web 资产禁止 source map 与内嵌 `sourcesContent`。
- `desktop/bootstrap/manifest.json` 只由正式打包前的 `desktop/prepare-bootstrap.cjs` 生成，不得手写或提交伪造文件。
- `desktop/assets/runtime-manifest.json` 必须保持签名有效；运行时版本变更必须更新版本契约、组件哈希并重新签名。

## Local evidence

`reports/`、`.real-api-*`、`.runtime-*` 和视觉/部署临时目录是本地副产物。需要共享时只提交脱敏摘要，格式见 `docs/LOCAL_REPORT_POLICY.md`。

发现密钥泄漏时应立即撤销密钥、清除未发布副产物并提交不含密钥的事件摘要；不要把原密钥复制到 issue、PR 或聊天记录。
