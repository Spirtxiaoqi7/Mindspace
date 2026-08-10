# 本地报告与真实 API 证据规范

> 状态：current。

以下内容仅是本地副产物：`reports/`、`.real-api-*`、`.runtime-*`、`.test-tmp*`、`.tmp*`、视觉对比目录、部署暂存目录、真实 API 原始日志和成人内容回归正文。它们不得进入提交、CI artifact、Core 更新包或安装器。

允许提交的脱敏摘要只包含：测试日期、产品版本、provider/model 名称、场景类别、调用次数、成功/失败数量、耗时分位数、错误类别、断言结论和不含正文的对象 ID。禁止 API 密钥、Authorization header、完整 prompt、完整回复、用户姓名、角色私密正文、附件内容和可逆哈希。

若必须给出失败样例，只写最小结构和错误分类，例如 `cards_batch=second_half, parse_error=truncated_json`；不要复制用户原文。
