---
status: accepted
scope: runtime-home
last_reviewed: 2026-08-11
---

# ADR 0001: Runtime Home Is the Single Path Root / 运行时 Home 是唯一路径根

## 中文

### 背景

安装包、可替换应用代码与用户数据具有不同生命周期。部署层负责提供运行时根；仓储和业务模块不得自行猜测根目录，也不得把用户状态写入源码或安装目录。

### 决策

规范根为 `home`，且 `runtime_dir = home`。所有运行时路径从该值直接派生：`data = runtime_dir / "data"`、`config = runtime_dir / "config"`、`logs = runtime_dir / "logs"`。`data`、`config`、`logs` 均只追加一次，任何调用方不得再次拼接同名目录。

### 后果

数据、配置和日志的位置可预测，部署、迁移、备份与诊断共享同一布局；应用更新不会覆盖用户数据。所有路径消费者必须接收或使用已解析的根，而不是重新发现路径。

### 禁止回退

禁止把 `runtime_dir` 解释为 `<home>/runtime`，禁止对 `data`、`config` 或 `logs` 进行二次拼接，禁止将运行时数据写入源码树或安装应用目录，禁止由仓储自行探测部署根。

## English

### Context

The installer, replaceable application code, and user data have different lifecycles. The deployment layer supplies the runtime root; repositories and business modules must not rediscover it or write user state into source or installation directories.

### Decision

The canonical root is `home`, and `runtime_dir = home`. Every runtime path is derived directly from it: `data = runtime_dir / "data"`, `config = runtime_dir / "config"`, and `logs = runtime_dir / "logs"`. `data`, `config`, and `logs` are each appended exactly once; no caller may append the same segment again.

### Consequences

Data, configuration, and logs have predictable locations, so deployment, migration, backup, and diagnostics share one layout; application updates do not overwrite user data. Every path consumer must receive or use the resolved root rather than rediscovering a path.

### No Reversion

Do not interpret `runtime_dir` as `<home>/runtime`, append `data`, `config`, or `logs` twice, write runtime state into the source tree or installed application directory, or let repositories discover the deployment root.
