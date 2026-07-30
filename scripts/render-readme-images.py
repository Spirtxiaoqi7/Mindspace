from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


WIDTH = 1600
HEIGHT = 1000
HERO_HEIGHT = 900
INK = (67, 47, 41)
MUTED = (127, 106, 96)
ACCENT = (190, 101, 72)
JADE = (103, 157, 143)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        fy = y / max(1, height - 1)
        for x in range(width):
            fx = x / max(1, width - 1)
            warm = max(0.0, 1.0 - ((fx - 0.08) ** 2 + (fy - 0.85) ** 2) ** 0.5)
            cool = max(0.0, 1.0 - ((fx - 0.92) ** 2 + (fy - 0.12) ** 2) ** 0.5)
            base = (249, 245, 237)
            pixels[x, y] = (
                int(min(255, base[0] + 5 * warm - 9 * cool)),
                int(min(255, base[1] - 7 * warm + 7 * cool)),
                int(min(255, base[2] - 10 * warm + 9 * cool)),
            )
    return image


def rounded(source: Image.Image, radius: int = 24) -> Image.Image:
    source = source.convert("RGBA")
    mask = Image.new("L", source.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, source.width, source.height), radius=radius, fill=255)
    result = Image.new("RGBA", source.size)
    result.paste(source, (0, 0), mask)
    return result


def contain(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    fitted = ImageOps.contain(source.convert("RGB"), size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (250, 247, 241))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def paste_shadow(canvas: Image.Image, source: Image.Image, xy: tuple[int, int], radius: int = 26) -> None:
    x, y = xy
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (x + 12, y + 18, x + source.width + 12, y + source.height + 18),
        radius=radius,
        fill=(82, 56, 45, 65),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(rounded(source, radius), (x, y))


def draw_header(canvas: Image.Image, eyebrow: str, title: str, subtitle: str) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((64, 42, 304, 92), radius=25, fill=(255, 255, 255, 215), outline=(225, 207, 195, 255))
    draw.text((88, 57), "演示数据 · MINDSPACE 0.7.4", fill=ACCENT, font=font(19, True))
    draw.text((64, 125), eyebrow.upper(), fill=JADE, font=font(20, True))
    draw.text((64, 160), title, fill=INK, font=font(42, True))
    draw.text((64, 220), subtitle, fill=MUTED, font=font(23))


def framed_single(source: Image.Image, eyebrow: str, title: str, subtitle: str) -> Image.Image:
    canvas = gradient((WIDTH, HEIGHT)).convert("RGBA")
    draw_header(canvas, eyebrow, title, subtitle)
    image = contain(source, (1472, 690))
    paste_shadow(canvas, image, (64, 276))
    return canvas.convert("RGB")


def framed_pair(
    left: Image.Image,
    right: Image.Image,
    eyebrow: str,
    title: str,
    subtitle: str,
    left_label: str,
    right_label: str,
) -> Image.Image:
    canvas = gradient((WIDTH, HEIGHT)).convert("RGBA")
    draw_header(canvas, eyebrow, title, subtitle)
    draw = ImageDraw.Draw(canvas)
    left_image = contain(left, (716, 520))
    right_image = contain(right, (716, 520))
    paste_shadow(canvas, left_image, (64, 396), 22)
    paste_shadow(canvas, right_image, (820, 396), 22)
    draw.rounded_rectangle((82, 362, 282, 408), radius=23, fill=(255, 255, 255, 230))
    draw.rounded_rectangle((838, 362, 1068, 408), radius=23, fill=(255, 255, 255, 230))
    draw.text((104, 373), left_label, fill=INK, font=font(20, True))
    draw.text((860, 373), right_label, fill=INK, font=font(20, True))
    return canvas.convert("RGB")


def hero(mode: Image.Image, chat: Image.Image, launcher: Image.Image) -> Image.Image:
    canvas = gradient((WIDTH, HERO_HEIGHT)).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((64, 48, 346, 98), radius=25, fill=(255, 255, 255, 220), outline=(225, 207, 195, 255))
    draw.text((88, 63), "真实界面 · 隔离演示数据", fill=ACCENT, font=font(20, True))
    draw.text((64, 130), "让角色、记忆与声音", fill=INK, font=font(46, True))
    draw.text((64, 188), "在同一个本地空间里延续。", fill=INK, font=font(46, True))
    draw.text((66, 260), "Windows AI Companion", fill=MUTED, font=font(23))
    draw.text((66, 294), "LangGraph · RAG · Voice", fill=MUTED, font=font(21))

    mode_panel = contain(mode, (850, 478))
    chat_panel = contain(chat, (920, 518))
    launcher_panel = contain(launcher, (520, 335))
    paste_shadow(canvas, mode_panel, (80, 356), 22)
    paste_shadow(canvas, chat_panel, (600, 238), 22)
    paste_shadow(canvas, launcher_panel, (1012, 538), 20)
    return canvas.convert("RGB")


def save_webp(image: Image.Image, path: Path, quality: int = 86) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "WEBP", quality=quality, method=6)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render privacy-safe Mindspace README screenshots.")
    parser.add_argument("--runtime", type=Path, required=True, help="Directory containing readme-demo-*.png captures.")
    parser.add_argument("--launcher", type=Path, required=True, help="Isolated Launcher QA screenshot.")
    parser.add_argument("--output", type=Path, required=True, help="README image output directory.")
    args = parser.parse_args()

    sources = {
        "modes": Image.open(args.runtime / "readme-demo-mode.png"),
        "card": Image.open(args.runtime / "readme-demo-card-preview.png"),
        "chat": Image.open(args.runtime / "readme-demo-chat.png"),
        "chapters": Image.open(args.runtime / "readme-demo-chapters.png"),
        "memory": Image.open(args.runtime / "readme-demo-memory.png"),
        "prompt": Image.open(args.runtime / "readme-demo-prompt.png"),
        "voice": Image.open(args.runtime / "readme-demo-voice.png"),
        "launcher": Image.open(args.launcher),
    }

    outputs = {
        "hero.webp": hero(sources["modes"], sources["chat"], sources["launcher"]),
        "01-modes.webp": framed_single(
            sources["modes"],
            "START HERE",
            "从模式大厅选择一次新的相遇",
            "灵感抽卡和完整工作台共享角色库，但记忆与会话始终按角色隔离。",
        ),
        "02-draw.webp": framed_single(
            sources["card"],
            "INSPIRATION FORGE",
            "AI 辅助补全一张可编辑人物卡",
            "一次生成、JSON 校验、本地模板兜底；确认收藏前不会写入正式角色库。",
        ),
        "03-chat.webp": framed_single(
            sources["chat"],
            "CONTINUOUS COMPANIONSHIP",
            "对话、场景与共同经历在同一条关系线上",
            "近期原文、当前角色记忆和场景状态共同参与本轮编排。",
        ),
        "04-chapters.webp": framed_single(
            sources["chapters"],
            "SHARED CHAPTERS",
            "共同片段可见、可确认，也可以移出时间线",
            "叙事记录属于当前角色，不会自动升级为人物档案事实。",
        ),
        "05-memory-prompt.webp": framed_pair(
            sources["memory"],
            sources["prompt"],
            "TRUST & INSPECTION",
            "既能看见记住了什么，也能检查模型实际收到了什么",
            "结构化记忆与 Prompt 层级分别可见，默认脱敏并保留来源边界。",
            "记忆中心",
            "Prompt Inspector",
        ),
        "06-voice-launcher.webp": framed_pair(
            sources["voice"],
            sources["launcher"],
            "VOICE & RUNTIME",
            "声音是可选能力，文字聊天永远先可用",
            "通话与面对面使用独立语音协议；Launcher 负责组件、硬件与存储防呆。",
            "实时语音入口",
            "零环境 Launcher",
        ),
    }

    for name, image in outputs.items():
        save_webp(image, args.output / name)

    print(f"rendered={len(outputs)} output={args.output.resolve()}")


if __name__ == "__main__":
    main()
