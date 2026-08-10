> 文档状态：prototype。内容尚未成为当前产品承诺；当前权威见 `docs/INDEX.md`。

# Live2D 桌宠接入前环境与开销核验

## 1. 结论

Mindspace 已经是 Electron 应用，Chromium 已提供 JavaScript、Canvas、WebGL 和 GPU 进程。因此采用官方 Cubism SDK for Web 时：

- 不新增 Python 环境。
- 不新增 Node 运行环境；Node 只参与现有前端构建。
- 不新增后端服务或网络端口。
- 不新增数据库和模型下载器。
- 不需要 MotionSync Core；口型复用现有 TTS PCM 音量。
- 新增内容是静态 SDK、模型资源、一个透明 BrowserWindow 和少量 Launcher IPC/UI。

SDK 门禁已经解除：用户提供了官方 `CubismSdkForWeb-5-r.5`，真实 renderer 已完成本机接入和烟测。

- SDK 路径：`A:\Live2D\SDK\CubismSdkForWeb-5-r.5`
- 版本：`5-r.5`，创建标记 `20260401T182224+0900`
- 完整 SDK：291 个文件，26,140,222 字节，约 24.93 MiB
- SDK 目录清单 SHA-256：`c4f902c7edb53b9d0a424beed3f65647aef1526f7468fd5d357305f9e596aff1`
- 分发用 Core SHA-256：`8741f739779b5d5210872bd3d7d99f0f1e56e6c87409e7d26d6bb4b80aa1ef47`

## 2. 当前模型体积

运行时入口：

`A:\Live2D\g4\runtime-v24-three-layer-replace\mindspace-companion-v24.model3.json`

源导出不包含可编辑 `.cmo3` 和回退文件时：

- 运行文件数：5。
- 总体积：12,199,337 字节，约 11.63 MiB。
- 4096 纹理：约 11.50 MiB。
- `.moc3`：约 0.12 MiB。
- JSON 配置：不足 0.02 MiB。

安装包不应携带 `.cmo3`；它只作为源文件单独交付。

运行端使用 2048 纹理副本。它由批准的 4096 纹理使用 Lanczos 缩小得到，不改变 UV、网格、参数、绑定和动作；在 420 × 720 默认窗口与 720 × 1000 最大窗口中通过视觉烟测。4096 原图仅保留在交付外。

## 3. SDK 与安装包增量

| 项目 | 是否新增 | 当前可确认开销 |
|---|---|---|
| 完整 SDK 开发包 | 否，不随安装包 | 24.93 MiB，291 文件 |
| 实际打包 renderer、Core、Framework 与 2048 模型 | 是 | 3,015,952 字节，约 2.88 MiB，22 文件 |
| SDK/Framework 许可证 | 是 | 9,744 字节；随 Launcher 保存 |
| Live2D 安装资源合计 | 是 | 3,025,696 字节，约 2.89 MiB |
| Electron/Chromium | 否 | 已随 Launcher 存在 |
| Python/uv/PowerShell/Git | 否 | 桌宠不依赖这些运行时 |
| 后端端口 | 否 | 继续使用现有受限 IPC |
| MotionSync | 否 | 不引入 |
| 网络下载 | 否 | SDK 和模型随安装包离线携带 |

## 4. 运行时预算

官方 SDK for Web 使用 WebGL。官方 FAQ 说明 Cubism Framework 初始化内存至少按 16 MiB 对齐；单模型应以 16–32 MiB Core 初始化内存作为起点。

2026-08-03 在当前验收机上的 6–7 秒可见窗口实测：

| 指标 | 首版预算/门槛 |
|---|---|
| 空白透明 Electron 窗口基线 | 136.60 MiB Private Bytes |
| 4096 模型相对空白窗口增量 | 250.20 MiB；未采用 |
| 2048 模型相对空白窗口增量 | 149.38 MiB；低于 180 MiB 失败门槛 |
| 2048 桌宠完整 Electron 探针 | 285.98 MiB Private Bytes，388.42 MiB Working Set，4 个进程；真实 Mindspace 中 GPU/utility 进程可与 Launcher 共享 |
| GPU 显存增量 | 目标不高于 128 MiB；超过 256 MiB 判失败 |
| 可见动画 CPU | 约 0.61% 全机 CPU；通过低于 8% 门槛 |
| 可见前台帧率 | 5 秒平均 96.37 FPS；通过不低于 55 FPS 门槛 |
| 隐藏窗口 | Electron 实测节流至约 1.58 FPS |
| 完全隐藏桌宠 | 停止 RAF、物理和计时器 |
| 模型加载失败 | 隐藏桌宠并返回诊断，不影响其他功能 |

Windows WDDM 下 `nvidia-smi pmon` 能识别 Electron GPU 进程，但未返回可靠的逐进程显存 MiB；因此 GPU 显存仍列为待长期运行测试项，不伪造数值。

## 5. 当前验收机

- Windows 11 Pro `10.0.22621`。
- CPU：Intel Core i5-14600KF。
- 内存：31.8 GiB；核验时空闲约 19.5 GiB。
- GPU：NVIDIA GeForce RTX 5060 Ti。
- `nvidia-smi` 报告显存：16,311 MiB。

该机器远高于首版单模型运行需求，但最终仍要验证低配机或通过硬件策略自动降帧。

## 6. 推荐架构

1. Launcher 页面提供实时角色预览、启用/禁用、点击穿透和放到桌面控制。
2. 桌宠使用独立透明 `BrowserWindow`，本地加载静态页面。
3. 默认约 420 × 720，置顶、无边框、透明，不创建服务端口。
4. 主进程只接受白名单状态：显示、隐藏、穿透、重置位置、受限渲染状态。
5. 窗口位置、尺寸、显示器和穿透状态写入 Launcher 配置。
6. 关闭 Launcher 或产品窗口后桌宠继续随托盘常驻；退出 Mindspace 时一起关闭。
7. 模型或 SDK 缺失时 Launcher 显示“桌宠资源未就绪”，不阻塞 Core 启动。

## 7. 许可门禁

- 开发前：用户在官方 Cubism SDK for Web 下载页阅读并接受 Proprietary/Open Software License Agreement，下载最新稳定版而非 alpha/beta。
- 接入时：记录 SDK 精确版本、压缩包 SHA-256、使用到的文件和许可证文本。
- 本机开发验收可以先进行。
- Mindspace 是安装在终端设备上的 AI/聊天机器人界面；公开发布前必须按 Live2D 官方 AI/Chatbot 与 Expandable Application 流程重新判断并完成适用许可。

官方入口：

- https://www.live2d.com/en/sdk/download/web/
- https://www.live2d.com/en/sdk/license/
- https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/

## 8. 已完成与待完成的测量

已完成：空白窗口基线、4096/2048 对照、透明 alpha、模型加载、可见/隐藏 FPS、短时 CPU/内存、完整静态构建。

待本机交互验收：连续运行五分钟、拖拽、点击穿透、多显示器拔插、托盘恢复、退出计时器回收以及产品窗口切换。
