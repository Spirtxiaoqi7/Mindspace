# 0.8.3 验证门禁

> 状态：current。自动门禁不访问真实 API、不读取用户数据、不执行正式打包。

## 治理

```powershell
node scripts/verify-version-consistency.mjs
node scripts/sync-gpt-sovits-catalog.mjs --check
node scripts/verify-repository-policy.mjs
```

覆盖版本消费者、锁文件根清单、Docker frozen 安装、source map、秘密、发布 allowlist、旧路径、历史脚本和 bootstrap 责任。

## 后端

```powershell
uv sync --frozen --extra dev
uv run ruff check src tests
uv run pytest -q
```

模型列表、durable run、provider/tool attempt、命格 6+6、端口/更新安全等测试均属于门禁。

## 前端与桌面

```powershell
cd frontend
npm ci
npm run check
npm test
npm run build

cd ..\desktop
npm ci
npm test
npm run check
```

Windows CI 额外运行 `scripts/build-update.ps1 -Version 0.8.3 -SkipBuild -DryRun`，验证 PowerShell 路径和 allowlist，不产出正式包。

## 手工真实 API

真实 API、成人内容和用户私密对话只允许隔离、本地、显式执行。输出必须留在忽略目录；可提交内容仅为脱敏统计摘要。
