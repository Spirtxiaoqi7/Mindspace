> 文档状态：prototype。内容尚未成为当前产品承诺；当前权威见 `docs/INDEX.md`。

# `.mindspace-card` 角色卡包格式

卡包是 ZIP 容器，扩展名固定为 `.mindspace-card`：

```text
manifest.json
ai-profile.json
avatar.webp  # 可选，也可为 png / jpg / gif
```

卡包不包含聊天记录、长期记忆、API Key、全局用户档案、运行状态或日志。导入永远创建新的
本地角色 UUID，不信任卡包内部 ID。

`manifest.json` 必须包含：

- `format: "mindspace-card"`
- `schema_version: "1.0.0"`
- 显示名称、性别、关系与角色专属用户称呼
- 每个负载文件的准确字节数和 SHA-256

Core 限制整个包不超过 10MiB、单文件不超过 5MiB、文件数量不超过 4，并拒绝绝对路径、
`..` 路径穿越、目录项、未知文件、超长文件名、校验和不符和不可识别头像。头像还会校验
文件扩展名与实际魔数，避免只改后缀的伪装文件。

导出文件名同时提供 ASCII 回退和 RFC 5987 UTF-8 名称，兼容 Windows 与浏览器下载。
