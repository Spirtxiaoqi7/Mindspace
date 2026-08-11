---
status: accepted
scope: modular-boundaries
last_reviewed: 2026-08-11
---

# ADR 0002: Preserve Modular-Monolith Boundaries / 保持模块化单体边界

## 中文

### 背景

Mindspace 以单仓库、单一桌面发布交付。前端、Desktop 与 Core 可协同演进，但无边界的跨层导入会把组合入口、存储实现和宿主细节扩散为隐式依赖。

### 决策

保持模块化单体而非拆分微服务。`desktop/main.cjs` 与 `bootstrap.py` 是各自运行面的组合根，不承载产品业务实现。前端由 `app/**` 组合、`features/**` 持有功能、`shared/**` 仅提供业务中立能力，功能只经最小 `index.ts` 接口暴露。后端依赖方向为入口到组合或路由、再到 feature 或 application、再到 models/ports/graph；具体 adapter 只由组合根注入。

### 后果

Desktop 负责窗口、Core 生命周期、更新和受控桥接，不持有产品状态或泄露秘密；API、应用服务、图节点与 adapter 的职责可独立替换和测试。跨层能力必须先定义窄接口，再由功能出口或组合根装配。

### 禁止回退

禁止功能之间直接互相导入，禁止功能反向依赖应用壳，禁止 application、路由或图节点选择具体 adapter 或穿透其私有实现，禁止 controller 导入 `main.cjs` 或共享彼此私有状态，禁止把组合根重新扩张为通用实现文件。

## English

### Context

Mindspace ships as one repository and one desktop release. Frontend, Desktop, and Core may evolve together, but unbounded cross-layer imports turn composition roots, storage details, and host details into implicit dependencies.

### Decision

Keep a modular monolith rather than splitting into microservices. `desktop/main.cjs` and `bootstrap.py` are composition roots for their respective runtime surfaces and do not contain product implementations. The frontend uses `app/**` for composition, `features/**` for product capabilities, and `shared/**` only for business-neutral capability; features expose only minimal `index.ts` interfaces. Backend dependencies flow from entry points to composition or routes, then to feature or application, then to models/ports/graph; concrete adapters are injected only by a composition root.

### Consequences

Desktop owns windows, Core lifecycle, updates, and controlled bridges, but not product state or secrets; APIs, application services, graph nodes, and adapters remain independently replaceable and testable. A cross-layer capability must define a narrow interface first and be composed through a feature export or composition root.

### No Reversion

Do not directly import between features, make features depend back on the application shell, let application services, routes, or graph nodes select or reach through concrete adapter-private implementations, let controllers import `main.cjs` or share private state, or grow a composition root back into a general implementation file.
