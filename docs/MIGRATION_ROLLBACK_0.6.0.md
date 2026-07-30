# 0.6.0 迁移与回滚

## 首次迁移

Core 启动时检查 `migration:characters:0.6.0`。未完成时先将旧 AI 档案、运行状态、头像配置
和会话 JSON 投影复制到：

```text
data/backups/character-migration-0.6.0/
```

随后在 SQLite 事务中创建“原有角色”，完整保留名称、性别、档案、头像和状态，并将所有
未绑定旧会话绑定到该角色。用户档案仍保持全局。事务成功后才保存迁移完成文档；重复启动
不会重复创建角色或重复绑定。

数据库写入失败会整体 rollback，不留下角色或完成标记。文件投影不是权威数据；若投影写入
失败，Core 记录 projection failure，并可从 SQLite 重建。迁移前备份不会自动删除。

## Core 更新回滚

Launcher 下载签名 Manifest，校验 Ed25519、SHA-256 和准确字节数，再切换 Core。健康检查
失败时使用 rollback token 恢复上一 Core 目录。已迁移用户数据不做在线降级；若发现迁移
缺陷，发布更高 Sequence 且 `rollout=0` 的 Catalog 停止新增更新，并发布 0.6.1 修复。

禁止降低版本号、复用 Sequence 或覆盖已有服务器版本目录。服务器只提供 Core ZIP、
签名 Manifest 和渠道 Catalog，不存储模型、WSL、第三方运行时、用户数据或签名私钥。
