> 文档状态：historical。仅保留历史证据，不得作为当前操作说明；当前权威见 `docs/INDEX.md`。

# 0.7.0 共同篇章美术预览来源

状态：`approved_2026-07-30`，已进入正式资源扩产。

这些位图为本项目本轮原创生成，不来自妹居物语或其他产品资源。SVG 图标和边框为仓库内原生
矢量绘制。所有预览均禁止用作用户头像或私人角色卡样例。

## 统一视觉提示

> Original anime watercolor scrapbook illustration for Mindspace, refined Chinese stationery
> aesthetic, warm parchment, muted terracotta and jade palette, delicate ink linework, soft
> cinematic lighting, elegant but lived-in, generous negative space, no readable text, no logo,
> no UI screenshot, no copyrighted character.

## 位图变化项

- `scene-riverside-preview.webp`：秋日晚间河畔步道、暖色路灯、远处城市与两人同行的留白。
- `scene-rainy-room-preview.webp`：雨夜室内、窗面雨痕、两杯温热饮品、安静陪伴氛围。
- `state-journal-empty-preview.webp`：空白日记、丝带、干花与尚未开始的留白。
- `state-journal-generating-preview.webp`：打开的日记本、星月纸饰、正在整理片段的轻盈动势。
- `state-journal-recover-preview.webp`：被风掀起的纸页、回形针与可恢复草稿的温和中断感。
- `state-moment-saved-preview.webp`：典藏盒、信封、压花与心形封蜡，表示已保存片段。

生成后统一转为 WebP，并按 Manifest 记录实际像素、文件字节数和 SHA-256。未经视觉审核，
不得以这些预览为模板批量扩产。

## 扩产记录

- 17 张新增场景使用内置 imagegen 逐张生成，统一采用上述视觉提示，并分别约束春日公园、
  落日天台、夜市、图书馆、海边、山间木屋、雪窗、夏夜阳台、秋日列车、厨房、观星原野、
  美术馆、花店、雨后旧街、湖畔、夏祭和深夜车内的具体构图。
- “咖啡馆角落”在线生成两次均遇到网络错误，按失败上限停止重试，改用同一配色的本地原创
  插画，不依赖外部图片或第三方素材。
- 图标、贴纸、边框、纹理、状态插画和日记封面由
  `scripts/build_art_library.py` 确定性生成；小图标使用 `64×64` 画布和
  `viewBox="4 4 56 56"` 紧裁剪。
- 正式场景统一安全裁剪到 `960×540` 并压缩为 WebP；扩产后的整个 `archive` 目录由脚本
  校验为 15MB 以下。
