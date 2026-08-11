---
status: current
scope: desktop-runtime
last_reviewed: 2026-08-11
---

# 桌面运行时架构 / Desktop Runtime Architecture

## 中文

### 1. 部署边界

桌面产品由 Electron 桌面壳和 Core 服务组成。Electron 负责桌面交互与受限桥接；Core 承载 API、LangGraph、仓储、模型适配和运行时任务。二者通过明确的本地服务/预加载边界协作，业务逻辑不得依赖渲染进程直接访问文件、令牌或 Core 内部实现。

安装目录、源码目录和用户运行数据是三个不同的边界。安装包用于交付应用，源码树用于开发，用户私有运行根用于可变数据、配置、日志、模型运行状态和恢复材料。不得把用户档案、会话、知识库或运行数据库写回安装包或源码目录。

### 2. 运行根目录与启动

`MINDSPACE_RUNTIME_DIR` 是 Core 运行数据根的权威配置。桌面正式版由 Launcher 将其指向用户私有数据目录；排查路径问题前必须先确认该实际值，而不能根据当前工作目录、打包目录或旧默认值推断。

Launcher 负责以受控方式启动和发现 Core，并把连接信息交给桌面壳。前端请求必须经过既定 Electron/preload 请求路径；若 Core 启用了令牌保护，客户端必须发送 `X-Mindspace-Token`。仅设置全局 `MINDSPACE_API_TOKEN` 而未让打包客户端携带该请求头，会使客户端请求被拒绝。

### 3. 数据与权限隔离

Electron 渲染层只能使用最小化、显式暴露的 API；preload 是渲染层与本地能力之间的边界。Core 的文件、数据库、服务令牌、模型配置和管理接口不得直接暴露给页面脚本。Core 只从运行根读取和写入用户数据，并以仓储和 API 边界实施 revision、路径、事务和审计规则。

安装或热更新不得覆盖用户数据。版本更新、Core 更新和桌面 UI 更新都必须把交付物与运行根分离；回滚应用版本也不得隐式回滚用户会话、档案或数据库。

### 4. 诊断与故障恢复

桌面故障首先区分四类：Electron 壳、Launcher/Core 进程、本地 API 认证/连通性、运行根数据。诊断必须报告实际运行根、Core 地址或发现状态、令牌请求路径以及相关日志位置，不能只因构建成功或依赖存在就断言桌面运行正常。

Core 启动失败、健康检查失败或连接发现失败时，桌面层应显示可诊断的失败状态，而不是把失败伪装成离线成功。恢复时优先修复启动配置、端口/令牌传递和运行根可访问性；不得自动清空、重建或迁移用户数据来掩盖问题。数据损坏或迁移问题应沿用仓储备份、原子写和受控迁移路径恢复。

### 5. 维护规则

修改 Electron、preload 或 Core 接口时，必须同时检查真实请求路径、认证头、运行根和打包后的行为。不得仅凭开发服务器可用就认定桌面版可用。新增桌面能力应维持最小权限原则：渲染层提出有限请求，preload 验证和转发，Core 在服务端执行并审计。

`<repo>` 是可编辑开发源；`<home>` 是桌面运行/部署目标。对运行目录的观察不能替代对源目录的开发修改，反之亦然。

## English

### 1. Deployment boundaries

The desktop product consists of the Electron desktop shell and the Core service. Electron owns desktop interaction and constrained bridging; Core owns the API, LangGraph, repositories, model adapters, and runtime jobs. They cooperate through explicit local-service and preload boundaries. Business logic must not rely on the renderer directly accessing files, tokens, or Core internals.

The installation directory, source directory, and user runtime data are three distinct boundaries. The installation package delivers the application, the source tree is for development, and the private user runtime root contains mutable data, configuration, logs, model runtime state, and recovery material. User profiles, sessions, knowledge bases, and runtime databases must not be written back to installation packages or source directories.

### 2. Runtime root and startup

`MINDSPACE_RUNTIME_DIR` is the authoritative configuration for the Core runtime-data root. The production desktop Launcher points it at a private user-data directory. Before diagnosing a path issue, confirm its actual value; do not infer it from the current working directory, packaged directory, or an old default.

The Launcher starts and discovers Core in a controlled manner, then provides connection information to the desktop shell. Frontend requests must follow the established Electron/preload request path. If Core enables token protection, the client must send `X-Mindspace-Token`. Setting a global `MINDSPACE_API_TOKEN` without making the packaged client send that header causes client requests to be rejected.

### 3. Data and permission isolation

The Electron renderer may use only minimal, explicitly exposed APIs; preload is the boundary between the renderer and local capabilities. Core files, databases, service tokens, model configuration, and administrative endpoints must not be exposed directly to page scripts. Core reads and writes user data only under the runtime root and applies revision, path, transaction, and audit rules through repository and API boundaries.

Installation and hot updates must not overwrite user data. Version updates, Core updates, and desktop UI updates must keep deliverables separate from the runtime root; rolling back an application version must not implicitly roll back user sessions, profiles, or databases.

### 4. Diagnosis and failure recovery

Desktop failures are first classified into four areas: the Electron shell, the Launcher/Core process, local API authentication/connectivity, and runtime-root data. Diagnosis must report the actual runtime root, Core address or discovery status, token request path, and relevant log location. A successful build or present dependency alone is not evidence that the desktop application runs correctly.

When Core startup, health checking, or connection discovery fails, the desktop layer should present a diagnosable failure state rather than disguising the failure as offline success. Recovery should first fix startup configuration, port/token propagation, and runtime-root accessibility; it must not automatically clear, recreate, or migrate user data to hide the problem. Data corruption or migration problems should recover through repository backups, atomic writes, and controlled migration paths.

### 5. Maintenance rules

When changing Electron, preload, or Core interfaces, inspect the real request path, authentication header, runtime root, and packaged behavior together. A working development server is not sufficient proof that the desktop release works. New desktop capabilities must preserve least privilege: the renderer makes bounded requests, preload validates and forwards them, and Core executes and audits them server-side.

`<repo>` is the editable development source; `<home>` is the desktop runtime/deployment target. Observing the runtime directory cannot substitute for development changes in the source directory, and vice versa.
