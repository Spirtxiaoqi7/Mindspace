# 0.9.0 分支与提交规范

> 状态：current。本文只定义协作规则，不执行 Git。

1. 每个分支只承担一个职责，例如 `test/model-list`、`docs/deprecation-register`、`build/version-contract`。
2. 提交中禁止生成包、bootstrap、报告、真实 API 日志、用户数据和视觉临时文件。
3. 业务 schema、数据库或 V2 扩展变化必须附迁移说明、回滚条件和旧数据测试。
4. 合并前通过版本/政策检查、后端测试、前端 check/test/build、desktop test/typecheck；Windows 脚本必须由 Windows job 验证。
5. 废弃接口先登记调用证据、替代物、删除门禁与目标版本；至少一个发布周期无调用且迁移测试通过后才能删除。
6. 不在功能分支顺手格式化全库或重生成无关资产；避免制造无法审阅的大 diff。
7. 前端、Python 和 API 契约必须通过 `docs/MODULAR_ARCHITECTURE.md` 定义的机械边界；历史例外名单只能减少，禁止为新功能扩充。
8. 修改 FastAPI 路由、请求模型或版本信息后必须重新生成 `contracts/openapi/mindspace.openapi.json`；CI 只接受与 `create_app()` 当前输出一致的快照。
9. OpenAPI 快照尚未覆盖全部 JSON 响应类型，不能据此删除前端手写 DTO，也不能将流、文件、音频或桌面 IPC 强行迁入普通 JSON Client。
10. 一般业务需求应限制在一个纵向功能模块内；需要同时修改三个以上功能模块时，提交说明必须列出跨模块原因、契约变化和回滚边界。
