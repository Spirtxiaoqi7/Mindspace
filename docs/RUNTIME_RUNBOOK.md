# 0.8.3 Runtime Runbook

> 状态：current。所有开发命令从 `A:\RAG\Mindspace-admin` 执行。

## 本地开发

```powershell
uv sync --frozen --extra dev
uv run mindspace-server
```

前端和桌面分别使用各自 lockfile 执行 `npm ci`。端口只能通过 `config/service-ports.json`、受支持环境变量或桌面设置桥解析，不在脚本中复制旧端口常量。

## 故障定位顺序

1. 检查 durable run 终态和 provider attempts。
2. 检查当前轮是否真的产生工具 request/attempt；没有则 UI 不得显示工具卡。
3. 检查会话 ID、回合 ID、摘要 ID 是否一致，禁止用全局临时文件恢复摘要。
4. 检查附件、引用和互动标签是否进入同一请求并被持久化。
5. 命格失败时检查前 6/后 6 独立状态，只重试失败半批。

## 禁止项

- 不从桌面普通 `settings.json` 复制密钥到测试运行时。
- 不在 CI 调用真实 provider。
- 不手写 bootstrap manifest，不修改签名 runtime manifest 后继续沿用旧签名。
- 不把 `reports/` 或用户数据当成发布输入。
