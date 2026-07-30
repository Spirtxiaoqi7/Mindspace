# README 展示资源说明

本目录保存 Mindspace GitHub 首页使用的产品展示图片。所有界面均来自当前
Core / Web `0.7.4` 与 Launcher `0.5.54`，没有重绘或伪造产品控件。

## 数据与隐私边界

- 截图于 2026-07-30 在隔离演示环境中生成。
- 演示角色固定为“岚音”，用户名称为“访客”，关系为“长期搭档”。
- 对话、共同篇章、记忆和 Prompt Inspector 内容均为合成演示数据。
- 演示服务不会读取 Mindspace 正式用户数据目录、私人角色卡、历史聊天或 API
  配置，也不会向外部模型发送请求。
- 原始截图与 Launcher QA 运行目录保留在本机忽略目录中，不提交到公开仓库；
  仓库只保存经过裁切和压缩的展示成品。

## 图片清单

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `hero.webp` | 模式大厅、聊天与 Launcher 首屏组合图 | 下列真实界面截图组合 |
| `01-modes.webp` | 模式大厅与典藏卡册 | 隔离的 0.7.4 React UI |
| `02-draw.webp` | 灵感抽卡与角色预览 | 隔离的 0.7.4 React UI |
| `03-chat.webp` | 带场景背景的主聊天页 | 隔离的 0.7.4 React UI |
| `04-chapters.webp` | 共同篇章与角色日记 | 隔离的 0.7.4 React UI |
| `05-memory-prompt.webp` | 记忆中心与 Prompt Inspector | 隔离的 0.7.4 React UI |
| `06-voice-launcher.webp` | 实时语音与桌面 Launcher | 隔离 UI 与安装 QA 截图 |

图片只增加文档用窗口框、圆角、阴影、渐变背景、“演示数据”标识和尺寸压缩；
不会添加应用中不存在的按钮、状态或能力。

## 头像与美术来源

演示头像使用项目美术 Manifest 中登记的
`frontend/public/characters/placeholder-1.webp`。其授权标识为：

```text
Mindspace Original AI-generated Asset
```

README 截图仅用于说明 Mindspace 产品界面。第三方模型、角色声音、参考音频和
用户导入素材不包含在这些展示资源中，其授权仍由各自来源决定。

## 可复现流程

启动前端开发服务：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

启动只提供合成数据的演示服务：

```powershell
node .\scripts\readme-demo-server.mjs
```

在 `http://127.0.0.1:5180/assets/` 捕获目标页面后，将原始图片放在仓库忽略的
`runtime/` 目录，再执行：

```powershell
python .\scripts\render-readme-images.py `
  --runtime .\runtime `
  --launcher .\runtime\<isolated-launcher-qa>\home\launcher-first-start.png `
  --output .\docs\readme
node .\scripts\update-readme-history.mjs
```

发布前必须再次检查成品中不存在真实用户名、本机路径、API Key、私人头像和真实
对话内容。
