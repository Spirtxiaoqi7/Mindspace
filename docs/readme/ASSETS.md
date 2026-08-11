---
status: current
scope: readme-assets
last_reviewed: 2026-08-11
---

# README 展示资源 / README Showcase Assets

## 中文

### 用途与截图来源

本目录保存 Mindspace GitHub 首页使用的产品展示图片。图片来自 Core / Web `0.7.4` 和 Launcher `0.5.54` 的历史隔离演示截图，作为当时的截图来源记录；它们不表示或暗示当前 UI 版本。所有截图均使用真实界面，没有重绘或伪造产品控件。

### 数据与隐私边界

- 截图于 2026-07-30 在隔离演示环境中生成。
- 演示角色固定为“岚音”，用户名称为“访客”，关系为“长期搭档”。
- 对话、共同篇章、记忆和 Prompt Inspector 内容均为合成演示数据。
- 演示服务不会读取 Mindspace 正式用户数据目录、私人角色卡、历史聊天或 API 配置，也不会向外部模型发送请求。
- 原始截图与 Launcher QA 运行目录保留在本机忽略目录中，不提交到公开仓库；仓库只保存经过裁切和压缩的展示成品。

### 图片清单

| 文件 | 内容 | 历史截图来源 |
| --- | --- | --- |
| `hero.webp` | 模式大厅、聊天与 Launcher 首屏组合图 | 下列真实界面截图组合 |
| `01-modes.webp` | 模式大厅与典藏卡册 | 隔离的 0.7.4 React UI |
| `02-draw.webp` | 灵感抽卡与角色预览 | 隔离的 0.7.4 React UI |
| `03-chat.webp` | 带场景背景的主聊天页 | 隔离的 0.7.4 React UI |
| `04-chapters.webp` | 共同篇章与角色日记 | 隔离的 0.7.4 React UI |
| `05-memory-prompt.webp` | 记忆中心与 Prompt Inspector | 隔离的 0.7.4 React UI |
| `06-voice-launcher.webp` | 实时语音与桌面 Launcher | 隔离 UI 与安装 QA 截图 |
| `github-render-desktop.png` | GitHub 分支页面的实际 README 首屏 | GitHub 公开页面验收 |

图片只增加文档用窗口框、圆角、阴影、渐变背景、“演示数据”标识和尺寸压缩；不会添加应用中不存在的按钮、状态或能力。

### 头像与美术来源及授权

演示头像使用项目美术 Manifest 中登记的 `frontend/public/characters/placeholder-1.webp`。其授权标识为：

```text
Mindspace Original AI-generated Asset
```

README 截图仅用于说明 Mindspace 产品界面。第三方模型、角色声音、参考音频和用户导入素材不包含在这些展示资源中，其授权仍由各自来源决定。

### 可复现流程

启动前端开发服务：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

启动只提供合成数据的演示服务：

```powershell
node .\scripts\readme-demo-server.mjs
```

在 `http://127.0.0.1:5180/assets/` 捕获目标页面后，将原始图片放在仓库忽略的 `runtime/` 目录，再执行：

```powershell
python .\scripts\render-readme-images.py `
  --runtime .\runtime `
  --launcher .\runtime\<isolated-launcher-qa>\home\launcher-first-start.png `
  --output .\docs\readme
node .\scripts\update-readme-history.mjs
```

发布前必须再次检查成品中不存在真实用户名、本机路径、API Key、私人头像和真实对话内容。

`github-render-desktop.png` 只用于记录发布验收，不在 README 正文中递归展示。

## English

### Purpose and screenshot provenance

This directory contains product showcase images for the Mindspace GitHub landing page. The images originated from historical isolated demonstration screenshots of Core / Web `0.7.4` and Launcher `0.5.54`; those versions identify the screenshot source only and do not represent or imply the current UI version. Every screenshot uses a real interface, with no redrawn or fabricated product controls.

### Data and privacy boundary

- Screenshots were generated in an isolated demonstration environment on 2026-07-30.
- The demonstration character is fixed as "岚音"; the user name is "访客"; the relationship is "长期搭档".
- Conversation, shared chapters, memory, and Prompt Inspector content are all synthetic demonstration data.
- The demonstration service does not read Mindspace production user-data directories, private character cards, chat history, or API configuration, and it does not send requests to external models.
- Source screenshots and the Launcher QA runtime directory remain in locally ignored directories and are not committed to the public repository. The repository contains only cropped and compressed showcase outputs.

### Image inventory

| File | Content | Historical screenshot source |
| --- | --- | --- |
| `hero.webp` | Composite of mode lobby, chat, and Launcher first screen | Composite of the real interface screenshots below |
| `01-modes.webp` | Mode lobby and collection cards | Isolated 0.7.4 React UI |
| `02-draw.webp` | Inspiration draw and character preview | Isolated 0.7.4 React UI |
| `03-chat.webp` | Main chat page with a scene background | Isolated 0.7.4 React UI |
| `04-chapters.webp` | Shared chapters and character diary | Isolated 0.7.4 React UI |
| `05-memory-prompt.webp` | Memory Center and Prompt Inspector | Isolated 0.7.4 React UI |
| `06-voice-launcher.webp` | Real-time voice and desktop Launcher | Isolated UI and installation-QA screenshots |
| `github-render-desktop.png` | Actual README first screen on the GitHub branch page | Public GitHub page release acceptance |

The images add only documentation window frames, rounded corners, shadows, gradient backgrounds, a "演示数据" marker, and size compression. They do not add buttons, states, or capabilities that do not exist in the application.

### Avatar, art source, and licensing

The demonstration avatar uses `frontend/public/characters/placeholder-1.webp`, registered in the project art Manifest. Its license marker is:

```text
Mindspace Original AI-generated Asset
```

The README screenshots are used only to describe the Mindspace product interface. Third-party models, character voices, reference audio, and user-imported assets are not included in these showcase resources; their licensing remains determined by their respective sources.

### Reproducible process

Start the frontend development service:

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

Start the demonstration service, which provides synthetic data only:

```powershell
node .\scripts\readme-demo-server.mjs
```

Capture the target page at `http://127.0.0.1:5180/assets/`, place the source images in the repository-ignored `runtime/` directory, then run:

```powershell
python .\scripts\render-readme-images.py `
  --runtime .\runtime `
  --launcher .\runtime\<isolated-launcher-qa>\home\launcher-first-start.png `
  --output .\docs\readme
node .\scripts\update-readme-history.mjs
```

Before publication, check again that the finished outputs contain no real user names, local paths, API keys, private avatars, or real conversation content.

`github-render-desktop.png` records release acceptance only and is not displayed recursively in the README body.
