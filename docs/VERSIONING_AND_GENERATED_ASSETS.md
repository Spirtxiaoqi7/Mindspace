# 版本与生成资产责任

> 状态：current。

`config/version.json` 是唯一版本契约。`product_version` 生成 Python、pyproject、uv 项目记录、frontend/desktop package 与 lock 根记录、payload 和 release history。签名运行时组件包有独立版本，但也在该契约中声明并校验。

```powershell
node scripts/sync-version.mjs
node scripts/verify-version-consistency.mjs
```

`desktop/assets/runtime-manifest.json` 是签名清单。更改运行时组件版本、URL 或哈希后必须重新签名；同步产品版本不得重写或伪造它。

`desktop/bootstrap/manifest.json` 不属于源码。正式打包前，`desktop/prepare-bootstrap.cjs` 从对应版本 Core zip 计算字节数和 SHA-256 后生成；缺少 Core zip 时必须失败，禁止预置占位 manifest。

GPT-SoVITS 音色关系：`config/gpt-sovits-voices.json` 是 Core 权威；`desktop/assets/gpt-sovits-voices.json` 是桌面生成镜像。使用 `scripts/sync-gpt-sovits-catalog.mjs` 同步，CI 用 `--check` 防漂移。

`payload.json` 是受管生成文件，来源是版本契约和 `config/core-release-allowlist.json`，必须提交并通过一致性校验。
