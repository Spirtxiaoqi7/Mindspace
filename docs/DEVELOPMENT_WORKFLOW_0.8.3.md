# 0.8.3 分支与提交规范

> 状态：current。本文只定义协作规则，不执行 Git。

1. 每个分支只承担一个职责，例如 `test/model-list`、`docs/deprecation-register`、`build/version-contract`。
2. 提交中禁止生成包、bootstrap、报告、真实 API 日志、用户数据和视觉临时文件。
3. 业务 schema、数据库或 V2 扩展变化必须附迁移说明、回滚条件和旧数据测试。
4. 合并前通过版本/政策检查、后端测试、前端 check/test/build、desktop test/typecheck；Windows 脚本必须由 Windows job 验证。
5. 废弃接口先登记调用证据、替代物、删除门禁与目标版本；至少一个发布周期无调用且迁移测试通过后才能删除。
6. 不在功能分支顺手格式化全库或重生成无关资产；避免制造无法审阅的大 diff。
