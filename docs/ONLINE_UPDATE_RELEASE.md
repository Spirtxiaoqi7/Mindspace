# Mindspace 在线更新发布

Mindspace Launcher 默认读取以下官方签名目录，普通用户不需要填写更新地址：

```text
https://douyinqijun.cn/downloads/mindspace/catalog/stable/windows-x64.json
```

日常业务、Prompt、RAG、TTS/ASR 编排和聊天前端变化只发布 Core 包，通常约 1–数 MiB。只有 Electron Launcher、安装器或更新器自身变化时才加入 `-IncludeLauncher`，避免用户反复下载完整安装包。

## 生成更新

稳定版 Core 更新：

```powershell
Set-Location A:\RAG\langgarph-rag

.\scripts\prepare-online-release.ps1 `
  -Version 0.4.1 `
  -Sequence 41 `
  -Channel stable `
  -Rollout 10 `
  -Title 'Mindspace 0.4.1' `
  -Notes '降低语音延迟|修复记忆写入|改进更新器'
```

Launcher 也发生变化时：

```powershell
.\scripts\prepare-online-release.ps1 `
  -Version 0.5.0 `
  -Sequence 50 `
  -Channel stable `
  -Rollout 5 `
  -MinimumLauncher 0.5.0 `
  -IncludeLauncher
```

发布脚本会拒绝未通过 Authenticode 验证的 Launcher。`-AllowUnsignedLauncher` 仅允许本地端到端测试，不得用于公开频道。

`Sequence` 必须永久递增，即使撤回版本也不能重复或降低。`Rollout` 可从 1、10、30、100 逐步增加；改变灰度比例时使用更高 Sequence 重新签名发布。

生成结果位于 `runtime\release-site\mindspace`。在独立 OSS/CDN 域名完成配置前，Core、Launcher 和签名目录统一由官网 `douyinqijun.cn/downloads/mindspace` 承载，避免 DNS 或 SPA 回退导致客户端取得 HTML。迁移 CDN 时必须先通过完整在线验收再切换客户端地址。

## 发布到本地 Web 根目录

```powershell
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -WebRoot D:\www\downloads\mindspace
```

版本文件会先复制，`catalog/stable/windows-x64.json` 最后原子替换，客户端不会看到半成品发布。

发布后必须验收，不能只看 HTTP 200：

```powershell
node .\scripts\verify-online-release.mjs --full
```

该命令会拒绝官网 SPA 返回的 HTML，并验证 JSON MIME、Ed25519 签名、Range、文件大小和 Core SHA-256。

## 通过 SSH 发布

发布机应使用 SSH 密钥；脚本不会保存服务器密码：

```powershell
.\scripts\publish-online-release.ps1 `
  -Channel stable `
  -Remote root@your-server `
  -RemoteRoot /var/www/downloads/mindspace
```

公开发布前可先上传完整暂存版本。该命令不会修改官网链接、Launcher feed 或 stable 清单：

```powershell
.\scripts\publish-online-release-interactive.ps1 -Channel stable -StagingOnly
```

暂存文件位于 `/downloads/mindspace/staging/<version>/`，完成公网大小、哈希和安装验证后再执行正式发布。

## CDN/服务器要求

- HTTPS，并支持 GET、HEAD 和 Range。
- `.partial` 续传必须返回 `206 Partial Content`。
- 版本文件使用 `Cache-Control: public, max-age=31536000, immutable`。
- `catalog/*/*.json` 和 `latest.yml` 使用 `Cache-Control: no-cache` 或不超过 60 秒。
- 允许传输 `.exe`、`.blockmap`、`.zip`、`.json` 和 `.yml`。
- Launcher EXE 必须使用受信任的 Windows Authenticode 证书签名。

Nginx 示例：

```nginx
location /mindspace/ {
    root /var/www/downloads;
    add_header Accept-Ranges bytes always;
    try_files $uri =404;
}

location ~ ^/mindspace/(catalog/.+\.json|launcher/.+/latest\.yml)$ {
    root /var/www/downloads;
    add_header Cache-Control "no-cache" always;
    add_header Accept-Ranges bytes always;
    try_files $uri =404;
}
```

## 客户端行为

1. 启动 5 秒后检查，之后每 6 小时检查。
2. 验证 Ed25519 目录签名、频道、Sequence 和灰度资格。
3. Launcher 更新优先于 Core 更新。
4. Core 更新默认后台下载，支持断点续传、暂停、继续和清除。
5. 下载后验证声明大小和 SHA-256。
6. Core 安装失败时恢复备份；Launcher 使用 NSIS 差分更新并验证 Authenticode 签名。

私钥 `runtime/update-keys/private.pem` 只留在发布机，严禁上传到官网、OSS、安装包或源码仓库。
