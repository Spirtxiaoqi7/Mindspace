"""Build the Mindspace 0.7 art library and its signed-data-ready manifest.

The script intentionally separates expensive concept generation from routine
product builds:

* scene source PNG files live in ``.tmp/art-expansion/raw`` during curation;
* accepted scenes are cropped and compressed into distributable WebP files;
* icons, stickers, frames, textures, state art and journal covers are rebuilt
  deterministically from code, so a release never depends on an online model;
* existing 0.6 asset IDs remain in the manifest for backwards compatibility.

Run from the repository root with ``uv run python scripts/build_art_library.py``.
Pillow is a build-time dependency only; Core never imports it at runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "frontend" / "public"
ARCHIVE_ROOT = PUBLIC_ROOT / "archive"
RAW_ROOT = ROOT / ".tmp" / "art-expansion" / "raw"
MANIFEST_PATH = ARCHIVE_ROOT / "manifest.json"

TERRACOTTA = "#bd6d57"
TERRACOTTA_DARK = "#925049"
JADE = "#739b8e"
JADE_DARK = "#476f66"
PARCHMENT = "#fff7e9"
INK = "#5b4037"
GOLD = "#c7a36b"


ICON_IDS = [
    "icon-journal",
    "heart-empty",
    "heart-trace",
    "heart-warm",
    "heart-glow",
    "activity-scene",
    "activity-questions",
    "activity-story",
    "activity-walk",
    "activity-night",
    "activity-indoor",
    "chapter-journal",
    "chapter-moments",
    "chapter-activity",
    "chapter-collect",
    "chapter-edit",
    "chapter-delete",
    "chapter-regenerate",
    "chapter-back",
    "chapter-filter",
    "chapter-search",
    "moment-chat",
    "moment-heart-keepsake",
    "moment-date",
    "moment-promise",
    "moment-discovery",
    "moment-travel",
    "moment-home",
    "moment-celebration",
    "moment-quiet",
    "state-empty",
    "state-loading",
    "state-error",
    "state-offline",
    "state-interrupted",
    "state-saved",
    "state-candidate",
    "state-archived",
    "icon-calendar",
    "icon-seal",
    "icon-ribbon",
    "icon-photo",
]

STICKER_IDS = [
    "sticker-heart-note",
    "sticker-moon",
    "sticker-star",
    "sticker-flower",
    "sticker-ribbon",
    "sticker-teacup",
    "sticker-umbrella",
    "sticker-cat",
    "sticker-letter",
    "sticker-camera",
    "sticker-leaf",
    "sticker-sparkle",
    "sticker-cloud",
    "sticker-lantern",
    "sticker-bookmark",
    "sticker-ticket",
    "sticker-seashell",
    "sticker-music",
    "sticker-firefly",
    "sticker-snowflake",
    "sticker-cookie",
    "sticker-key",
    "sticker-map-pin",
    "sticker-wax-seal",
]

FRAME_IDS = [
    "frame-journal-paper",
    "frame-journal-night",
    "frame-moment-line",
    "frame-moment-seal",
    "frame-activity-scene",
    "frame-activity-question",
    "frame-activity-story",
    "frame-dialog-soft",
    "frame-dialog-night",
    "frame-card-spring",
    "frame-card-summer",
    "frame-card-autumn",
    "frame-card-winter",
    "frame-popup-keepsake",
]

TEXTURE_IDS = [
    "texture-sakura",
    "texture-summer-night",
    "texture-autumn-leaf",
    "texture-winter-snow",
    "texture-rainy-glass",
    "texture-kraft",
    "texture-linen",
    "texture-cloud",
    "texture-firefly",
]

STATE_IDS = [
    "state-journal-empty",
    "state-journal-generating",
    "state-journal-recover",
    "state-moment-saved",
    "state-moment-empty",
    "state-moment-candidate",
    "state-activity-empty",
    "state-activity-active",
    "state-activity-completed",
    "state-offline",
    "state-asset-missing",
    "state-revision-conflict",
]

SCENE_IDS = [
    "scene-riverside",
    "scene-rainy-room",
    "scene-spring-park",
    "scene-sunset-rooftop",
    "scene-night-market",
    "scene-library-afternoon",
    "scene-seaside-dawn",
    "scene-mountain-cabin",
    "scene-snowy-window",
    "scene-summer-balcony",
    "scene-autumn-train",
    "scene-cafe-corner",
    "scene-kitchen-evening",
    "scene-stargazing-field",
    "scene-museum-hall",
    "scene-flower-shop",
    "scene-old-street",
    "scene-lakeside-picnic",
    "scene-festival-lantern",
    "scene-late-night-drive",
]

COVER_IDS = [
    "journal-cover-paper",
    "journal-cover-jade",
    "journal-cover-night",
    "journal-cover-spring",
    "journal-cover-summer",
    "journal-cover-autumn",
    "journal-cover-winter",
    "journal-cover-constellation",
]


def _write_text(path: Path, content: str) -> None:
    """Write UTF-8 only when content changes to keep rebuild diffs stable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8", newline="\n")


def _heart_path(scale: float = 1.0, x: float = 0, y: float = 0) -> str:
    return (
        f"M {x + 32 * scale:.2f} {y + 54 * scale:.2f} "
        f"C {x + 26 * scale:.2f} {y + 47 * scale:.2f}, "
        f"{x + 8 * scale:.2f} {y + 36 * scale:.2f}, "
        f"{x + 8 * scale:.2f} {y + 22 * scale:.2f} "
        f"C {x + 8 * scale:.2f} {y + 9 * scale:.2f}, "
        f"{x + 24 * scale:.2f} {y + 5 * scale:.2f}, "
        f"{x + 32 * scale:.2f} {y + 17 * scale:.2f} "
        f"C {x + 40 * scale:.2f} {y + 5 * scale:.2f}, "
        f"{x + 56 * scale:.2f} {y + 9 * scale:.2f}, "
        f"{x + 56 * scale:.2f} {y + 22 * scale:.2f} "
        f"C {x + 56 * scale:.2f} {y + 36 * scale:.2f}, "
        f"{x + 38 * scale:.2f} {y + 47 * scale:.2f}, "
        f"{x + 32 * scale:.2f} {y + 54 * scale:.2f} Z"
    )


def _glyph(name: str) -> str:
    """Return a compact semantic glyph for the shared icon language."""

    stroke = f'stroke="{INK}" stroke-width="3.2" stroke-linecap="round" '
    stroke += 'stroke-linejoin="round"'
    if "heart" in name or "collect" in name or "date" in name:
        fill = "none" if name.endswith(("empty", "trace")) else TERRACOTTA
        return f'<path d="{_heart_path()}" fill="{fill}" {stroke}/>'
    if any(token in name for token in ("journal", "story", "bookmark")):
        return (
            f'<path d="M12 13q11-5 20 2v37q-9-6-20-2zM52 13q-11-5-20 2v37q9-6 20-2z" '
            f'{stroke} fill="none"/>'
            f'<path d="M18 23h8M18 31h8M38 23h8" {stroke} fill="none"/>'
        )
    if any(token in name for token in ("chat", "questions")):
        return (
            f'<path d="M10 13h44v31H30l-12 9 3-9H10z" {stroke} fill="none"/>'
            f'<circle cx="22" cy="28" r="2" fill="{TERRACOTTA}"/>'
            f'<circle cx="32" cy="28" r="2" fill="{JADE}"/>'
            f'<circle cx="42" cy="28" r="2" fill="{GOLD}"/>'
        )
    if any(token in name for token in ("scene", "walk", "travel", "map")):
        return (
            f'<path d="M9 50l13-32 13 12 8-10 12 30z" {stroke} fill="none"/>'
            f'<circle cx="45" cy="14" r="5" fill="{GOLD}" stroke="{INK}" stroke-width="2"/>'
            f'<path d="M20 49q10-13 24 0" {stroke} fill="none"/>'
        )
    if any(token in name for token in ("night", "quiet", "offline")):
        return (
            f'<path d="M43 10a21 21 0 1 0 10 35A18 18 0 0 1 43 10z" fill="{JADE}" '
            f'stroke="{INK}" stroke-width="3"/>'
            f'<path d="M17 18l2 4 4 2-4 2-2 4-2-4-4-2 4-2z" fill="{GOLD}"/>'
        )
    if any(token in name for token in ("saved", "promise", "seal")):
        return (
            f'<circle cx="32" cy="32" r="22" fill="{PARCHMENT}" '
            f'stroke="{TERRACOTTA}" stroke-width="4"/>'
            f'<path d="M20 32l8 8 17-19" {stroke} fill="none"/>'
        )
    if any(token in name for token in ("error", "delete", "interrupted", "conflict")):
        return (
            f'<path d="M32 8l25 45H7z" fill="#f7ddd2" stroke="{TERRACOTTA_DARK}" stroke-width="3"/>'
            f'<path d="M32 22v15M32 45v1" {stroke} fill="none"/>'
        )
    if any(token in name for token in ("calendar", "activity", "candidate")):
        return (
            f'<rect x="10" y="15" width="44" height="39" rx="6" fill="{PARCHMENT}" '
            f'stroke="{INK}" stroke-width="3"/>'
            f'<path d="M10 26h44M20 10v10M44 10v10" {stroke} fill="none"/>'
            f'<circle cx="25" cy="37" r="3" fill="{TERRACOTTA}"/>'
            f'<circle cx="39" cy="37" r="3" fill="{JADE}"/>'
        )
    if any(token in name for token in ("loading", "regenerate")):
        return (
            f'<path d="M49 20a21 21 0 1 0 3 22" {stroke} fill="none"/>'
            f'<path d="M48 10l1 10-10-1" {stroke} fill="none"/>'
        )
    return (
        f'<path d="M32 8l7 15 17 2-12 12 3 17-15-8-15 8 3-17L8 25l17-2z" '
        f'fill="{GOLD}" stroke="{INK}" stroke-width="3"/>'
    )


def _icon_svg(name: str) -> str:
    extra = ""
    if name == "heart-trace":
        extra = (
            f'<path d="M18 41q14-10 28 0" stroke="{GOLD}" stroke-width="2.5" '
            'stroke-linecap="round" fill="none"/>'
        )
    if name == "heart-glow":
        extra = (
            f'<circle cx="32" cy="32" r="27" fill="none" stroke="{GOLD}" '
            'stroke-width="2" stroke-dasharray="3 5"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" '
        'viewBox="4 4 56 56" role="img">'
        f"{_glyph(name)}{extra}</svg>\n"
    )


def _sticker_svg(name: str, index: int) -> str:
    palettes = [
        ("#fff1e8", TERRACOTTA),
        ("#edf6f1", JADE),
        ("#fff6d9", GOLD),
        ("#e9edf8", "#68728f"),
    ]
    paper, accent = palettes[index % len(palettes)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" '
        'viewBox="0 0 128 128" role="img">'
        '<defs><filter id="s"><feDropShadow dx="0" dy="3" stdDeviation="3" '
        'flood-color="#5b4037" flood-opacity=".2"/></filter></defs>'
        f'<path d="M18 13Q64 2 110 17L119 64Q111 111 62 119Q14 111 8 64Z" '
        f'fill="{paper}" stroke="white" stroke-width="8" filter="url(#s)"/>'
        f'<circle cx="64" cy="64" r="38" fill="{accent}" opacity=".12"/>'
        f'<g transform="translate(32 32)">{_glyph(name)}</g>'
        f'<circle cx="{25 + (index * 17) % 78}" cy="{22 + (index * 11) % 84}" '
        f'r="3" fill="{accent}" opacity=".55"/></svg>\n'
    )


def _frame_svg(name: str, index: int) -> str:
    palette = [
        (PARCHMENT, TERRACOTTA, JADE),
        ("#f1f5ef", JADE_DARK, GOLD),
        ("#252b3a", "#d8b77b", TERRACOTTA),
        ("#fff3eb", "#a95855", "#d6aa7a"),
    ][index % 4]
    bg, line, accent = palette
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420" '
        'viewBox="0 0 640 420" preserveAspectRatio="none">'
        '<defs><filter id="d"><feDropShadow dx="0" dy="8" stdDeviation="10" '
        'flood-color="#5b4037" flood-opacity=".12"/></filter></defs>'
        f'<rect x="12" y="12" width="616" height="396" rx="28" fill="{bg}" '
        f'stroke="{line}" stroke-width="2" filter="url(#d)"/>'
        f'<rect x="26" y="26" width="588" height="368" rx="21" fill="none" '
        f'stroke="{accent}" stroke-width="1.5" stroke-dasharray="2 8"/>'
        f'<path d="M26 86Q75 26 140 26M500 394Q565 394 614 334" fill="none" '
        f'stroke="{accent}" stroke-width="7" opacity=".45"/>'
        f'<circle cx="46" cy="46" r="9" fill="{accent}" opacity=".7"/>'
        f'<circle cx="594" cy="374" r="9" fill="{accent}" opacity=".7"/>'
        "</svg>\n"
    )


def _paper_canvas(size: tuple[int, int], seed: int, base: tuple[int, int, int]) -> Image.Image:
    randomizer = random.Random(seed)
    image = Image.new("RGB", size, base)
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            noise = randomizer.randint(-7, 7)
            pixels[x, y] = tuple(max(0, min(255, channel + noise)) for channel in base)
    return image.filter(ImageFilter.GaussianBlur(0.25))


def _texture(name: str, index: int) -> Image.Image:
    bases = [
        (248, 231, 220),
        (35, 47, 61),
        (229, 210, 175),
        (230, 238, 239),
        (91, 106, 112),
        (202, 175, 133),
        (238, 227, 207),
        (223, 231, 229),
        (31, 48, 54),
    ]
    image = _paper_canvas((256, 256), 700 + index, bases[index])
    draw = ImageDraw.Draw(image, "RGBA")
    randomizer = random.Random(1700 + index)
    if "rainy" in name:
        for _ in range(60):
            x = randomizer.randint(0, 255)
            y = randomizer.randint(0, 255)
            draw.line((x, y, x - 7, y + 30), fill=(220, 240, 244, 80), width=2)
    elif "linen" in name:
        for offset in range(0, 256, 8):
            draw.line((offset, 0, offset, 256), fill=(110, 90, 70, 22), width=1)
            draw.line((0, offset, 256, offset), fill=(110, 90, 70, 18), width=1)
    elif "cloud" in name:
        for _ in range(18):
            x, y = randomizer.randint(-20, 230), randomizer.randint(0, 240)
            draw.ellipse((x, y, x + 70, y + 28), fill=(255, 255, 255, 38))
    else:
        colors = [(190, 91, 80, 80), (222, 175, 91, 80), (116, 158, 143, 75)]
        for _ in range(38):
            x, y = randomizer.randint(4, 252), randomizer.randint(4, 252)
            radius = randomizer.randint(1, 4)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colors[_ % 3])
    return image


def _state_art(name: str, index: int) -> Image.Image:
    image = _paper_canvas((640, 640), 3000 + index, (250, 242, 228))
    draw = ImageDraw.Draw(image, "RGBA")
    accent = [(185, 101, 84), (102, 142, 130), (196, 159, 99)][index % 3]
    draw.rounded_rectangle(
        (82, 110, 558, 526),
        radius=46,
        fill=(255, 251, 242, 225),
        outline=accent + (120,),
        width=5,
    )
    if "moment" in name:
        draw.rounded_rectangle((185, 205, 455, 390), 22, fill=(244, 224, 210, 255))
        draw.polygon([(185, 205), (320, 320), (455, 205)], fill=(255, 244, 232, 255))
        draw.ellipse((274, 340, 366, 432), fill=accent + (225,))
    elif "activity" in name:
        draw.ellipse((200, 180, 440, 420), fill=(222, 235, 227, 220))
        draw.line((246, 368, 320, 230, 394, 368), fill=accent + (255,), width=18)
        draw.ellipse((305, 206, 335, 236), fill=(224, 180, 92, 255))
    elif "offline" in name or "missing" in name or "conflict" in name:
        draw.polygon([(320, 170), (450, 410), (190, 410)], fill=(246, 220, 208, 255))
        draw.line((320, 245, 320, 335), fill=(130, 73, 66, 255), width=18)
        draw.ellipse((310, 360, 330, 380), fill=(130, 73, 66, 255))
    else:
        draw.rounded_rectangle((185, 180, 455, 440), 28, fill=(248, 235, 213, 255))
        draw.line((320, 185, 320, 435), fill=accent + (180,), width=5)
        for row in range(4):
            draw.line(
                (220, 245 + row * 42, 290, 245 + row * 42),
                fill=(103, 78, 66, 100),
                width=5,
            )
            draw.line(
                (350, 245 + row * 42, 420, 245 + row * 42),
                fill=(103, 78, 66, 100),
                width=5,
            )
    for angle in range(0, 360, 45):
        radians = math.radians(angle)
        x = 320 + math.cos(radians) * 250
        y = 320 + math.sin(radians) * 250
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=accent + (100,))
    return image


def _cover(name: str, index: int) -> Image.Image:
    bases = [
        (235, 218, 190),
        (116, 151, 137),
        (39, 45, 62),
        (239, 204, 203),
        (91, 137, 135),
        (184, 110, 72),
        (207, 221, 225),
        (43, 51, 72),
    ]
    image = _paper_canvas((512, 768), 5100 + index, bases[index])
    draw = ImageDraw.Draw(image, "RGBA")
    light = (255, 245, 221, 190)
    dark = (76, 52, 47, 180)
    draw.rounded_rectangle((34, 34, 478, 734), 30, outline=light, width=6)
    draw.rounded_rectangle((54, 54, 458, 714), 22, outline=dark, width=2)
    draw.ellipse((184, 260, 328, 404), fill=light, outline=dark, width=4)
    if "constellation" in name or "night" in name:
        randomizer = random.Random(9000 + index)
        points = [(randomizer.randint(100, 412), randomizer.randint(100, 650)) for _ in range(18)]
        for left, right in zip(points, points[1:], strict=False):
            draw.line((*left, *right), fill=(245, 220, 160, 80), width=2)
        for x, y in points:
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(251, 231, 171, 220))
    else:
        for offset in range(5):
            draw.arc(
                (164 - offset * 12, 240 - offset * 12, 348 + offset * 12, 424 + offset * 12),
                205,
                335,
                fill=dark,
                width=2,
            )
    draw.rectangle((72, 672, 440, 678), fill=light)
    return image


def _cafe_scene() -> Image.Image:
    image = _paper_canvas((960, 540), 7311, (231, 218, 195))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, 960, 540), fill=(218, 201, 172, 160))
    draw.rectangle(
        (70, 60, 580, 390), fill=(160, 183, 181, 255), outline=(83, 70, 61, 220), width=12
    )
    draw.rectangle((90, 80, 560, 370), fill=(190, 208, 207, 210))
    for x in range(120, 560, 90):
        draw.line((x, 80, x - 40, 370), fill=(240, 247, 244, 90), width=4)
    draw.rectangle((0, 405, 960, 540), fill=(112, 79, 59, 255))
    draw.ellipse((250, 300, 710, 440), fill=(111, 76, 55, 255))
    draw.rectangle((300, 370, 660, 500), fill=(102, 70, 52, 255))
    for x in (370, 535):
        draw.ellipse(
            (x, 300, x + 90, 350), fill=(248, 238, 218, 255), outline=(92, 69, 57, 255), width=4
        )
        draw.rectangle((x + 12, 322, x + 78, 380), fill=(248, 238, 218, 255))
        draw.arc((x + 62, 330, x + 100, 365), 265, 95, fill=(92, 69, 57, 255), width=5)
    draw.ellipse((458, 322, 530, 382), fill=(210, 147, 103, 255))
    draw.line((70, 405, 580, 405), fill=(77, 58, 51, 255), width=10)
    return image.filter(ImageFilter.GaussianBlur(0.35))


def _save_webp(image: Image.Image, path: Path, *, quality: int = 68) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "WEBP", quality=quality, method=6)


def _fit_scene(source: Path, destination: Path) -> None:
    with Image.open(source) as opened:
        image = ImageOps.fit(
            opened.convert("RGB"),
            (960, 540),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    _save_webp(image, destination, quality=66)


def build_files() -> None:
    """Create all deterministic assets and compress accepted scene sources."""

    for asset_id in ICON_IDS:
        _write_text(ARCHIVE_ROOT / "icons" / f"{asset_id}.svg", _icon_svg(asset_id))
    for index, asset_id in enumerate(STICKER_IDS):
        _write_text(
            ARCHIVE_ROOT / "stickers" / f"{asset_id}.svg",
            _sticker_svg(asset_id, index),
        )
    for index, asset_id in enumerate(FRAME_IDS):
        _write_text(
            ARCHIVE_ROOT / "frames" / f"{asset_id}.svg",
            _frame_svg(asset_id, index),
        )
    for index, asset_id in enumerate(TEXTURE_IDS):
        _save_webp(
            _texture(asset_id, index),
            ARCHIVE_ROOT / "textures" / f"{asset_id}.webp",
            quality=64,
        )

    canonical_previews = {
        "state-journal-empty": "state-journal-empty-preview.webp",
        "state-journal-generating": "state-journal-generating-preview.webp",
        "state-journal-recover": "state-journal-recover-preview.webp",
        "state-moment-saved": "state-moment-saved-preview.webp",
    }
    for index, asset_id in enumerate(STATE_IDS):
        destination = ARCHIVE_ROOT / "states" / f"{asset_id}.webp"
        preview_name = canonical_previews.get(asset_id)
        if preview_name:
            source = ARCHIVE_ROOT / "previews" / preview_name
            with Image.open(source) as opened:
                image = ImageOps.fit(
                    opened.convert("RGB"),
                    (640, 640),
                    method=Image.Resampling.LANCZOS,
                )
        else:
            image = _state_art(asset_id, index)
        _save_webp(image, destination, quality=68)

    scene_previews = {
        "scene-riverside": ARCHIVE_ROOT / "previews" / "scene-riverside-preview.webp",
        "scene-rainy-room": ARCHIVE_ROOT / "previews" / "scene-rainy-room-preview.webp",
    }
    for asset_id in SCENE_IDS:
        destination = ARCHIVE_ROOT / "scenes" / f"{asset_id}.webp"
        if asset_id == "scene-cafe-corner":
            _save_webp(_cafe_scene(), destination, quality=68)
            continue
        source = scene_previews.get(asset_id) or RAW_ROOT / f"{asset_id}.png"
        if source.exists():
            _fit_scene(source, destination)
        elif not destination.exists():
            raise FileNotFoundError(f"missing accepted source for {asset_id}: {source}")

    for index, asset_id in enumerate(COVER_IDS):
        _save_webp(
            _cover(asset_id, index),
            ARCHIVE_ROOT / "covers" / f"{asset_id}.webp",
            quality=70,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_file(asset_path: str) -> Path:
    clean = asset_path.split("#", 1)[0].removeprefix("/assets/")
    return PUBLIC_ROOT / clean


def _dimensions(path: Path) -> dict[str, int]:
    if path.suffix.lower() == ".svg":
        text = path.read_text(encoding="utf-8")
        width = re.search(r'\bwidth="(\d+)', text)
        height = re.search(r'\bheight="(\d+)', text)
        return {
            "width": int(width.group(1)) if width else 0,
            "height": int(height.group(1)) if height else 0,
        }
    if path.suffix.lower() in {".webp", ".png", ".jpg", ".jpeg"}:
        with Image.open(path) as opened:
            return {"width": opened.width, "height": opened.height}
    return {"width": 0, "height": 0}


def _record(
    asset_id: str,
    asset_type: str,
    path: str,
    usage: list[str],
    fallback: str,
    *,
    license_name: str = "Mindspace Original Assets",
    min_version: str = "0.7.0",
) -> dict[str, Any]:
    file_path = _asset_file(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"manifest asset is missing: {file_path}")
    return {
        "id": asset_id,
        "pack_id": "core",
        "theme": "anime-scrapbook",
        "usage": usage,
        "type": asset_type,
        "path": path,
        "dimensions": _dimensions(file_path),
        "bytes": file_path.stat().st_size,
        "min_app_version": min_version,
        "license": license_name,
        "sha256": _sha256(file_path),
        "fallback": fallback,
    }


def _upgrade_legacy(item: dict[str, Any]) -> dict[str, Any]:
    path = str(item["path"])
    file_path = _asset_file(path)
    return {
        **item,
        "pack_id": item.get("pack_id") or "core",
        "theme": item.get("theme") or "anime-scrapbook",
        "usage": item.get("usage") or [str(item.get("type") or "compatibility")],
        "dimensions": item.get("dimensions") or _dimensions(file_path),
        "bytes": file_path.stat().st_size,
        "min_app_version": item.get("min_app_version") or "0.6.0",
        "sha256": _sha256(file_path),
    }


def build_manifest() -> None:
    """Replace preview records with canonical assets and preserve 0.6 IDs."""

    current = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    generated_prefixes = (
        "/assets/archive/icons/",
        "/assets/archive/stickers/",
        "/assets/archive/frames/",
        "/assets/archive/textures/",
        "/assets/archive/states/",
        "/assets/archive/scenes/",
        "/assets/archive/covers/",
    )
    legacy = [
        _upgrade_legacy(item)
        for item in current.get("assets") or []
        if "/previews/" not in str(item.get("path") or "")
        and not str(item.get("path") or "").startswith(generated_prefixes)
    ]

    generated: list[dict[str, Any]] = []
    generated.extend(
        _record(
            asset_id,
            "heart-state" if asset_id.startswith("heart-") else "operation-icon",
            f"/assets/archive/icons/{asset_id}.svg",
            ["shared-chapters", "compact-ui"],
            "text:共同篇章" if asset_id.startswith("heart-") else "text:功能",
        )
        for asset_id in ICON_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "scrapbook-sticker",
            f"/assets/archive/stickers/{asset_id}.svg",
            ["journal-decoration", "moment-decoration"],
            "none",
        )
        for asset_id in STICKER_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "frame",
            f"/assets/archive/frames/{asset_id}.svg",
            ["journal-card", "moment-card", "activity-card"],
            "css:1px solid var(--line)",
        )
        for asset_id in FRAME_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "texture",
            f"/assets/archive/textures/{asset_id}.webp",
            ["page-background", "card-background"],
            "color:#fff7e9",
        )
        for asset_id in TEXTURE_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "state-illustration",
            f"/assets/archive/states/{asset_id}.webp",
            ["empty-state", "progress-state", "failure-state"],
            "text:当前没有可显示内容",
            license_name="Mindspace Original and AI-assisted Assets",
        )
        for asset_id in STATE_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "scene-thumbnail",
            f"/assets/archive/scenes/{asset_id}.webp",
            ["activity-scene", "scene-selection"],
            "color:#dfe9e2",
            license_name=(
                "Mindspace Original Assets"
                if asset_id == "scene-cafe-corner"
                else "Mindspace Original AI-generated Asset"
            ),
        )
        for asset_id in SCENE_IDS
    )
    generated.extend(
        _record(
            asset_id,
            "journal-cover",
            f"/assets/archive/covers/{asset_id}.webp",
            ["journal-cover", "journal-library"],
            "color:#eadbc2",
        )
        for asset_id in COVER_IDS
    )

    category_counts = {
        "svg_icons": 72,
        "scrapbook_stickers": len(STICKER_IDS),
        "frames": 16,
        "textures": 12,
        "state_illustrations": len(STATE_IDS),
        "scene_thumbnails": len(SCENE_IDS),
        "journal_covers": len(COVER_IDS),
        "compatibility_extras": 7,
    }
    manifest = {
        "schema_version": "2.0.0",
        "library_id": "mindspace-archive-0.7.0",
        "license": "Mindspace Original Assets",
        "approval_status": "approved_for_expansion",
        "defaults": {
            "pack_id": "core",
            "theme": "anime-scrapbook",
            "min_app_version": "0.6.0",
            "license": "Mindspace Original Assets",
        },
        "categories": category_counts,
        "packs": current.get("packs") or [],
        "preview_index": "/assets/archive/previews/index.json",
        "assets": [*legacy, *generated],
    }
    _write_text(
        MANIFEST_PATH,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )

    preview_path = ARCHIVE_ROOT / "previews" / "index.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    preview["approval_status"] = "approved_2026-07-30"
    preview["expanded_library"] = {
        "manifest": "/assets/archive/manifest.json",
        "asset_count": len(manifest["assets"]),
    }
    _write_text(
        preview_path,
        json.dumps(preview, ensure_ascii=False, indent=2) + "\n",
    )


def main() -> None:
    build_files()
    build_manifest()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    total_bytes = sum(path.stat().st_size for path in ARCHIVE_ROOT.rglob("*") if path.is_file())
    if total_bytes > 15 * 1024 * 1024:
        raise RuntimeError(f"built-in art library exceeds 15MB: {total_bytes}")
    if len(manifest["assets"]) != 171:
        raise RuntimeError(f"unexpected manifest asset count: {len(manifest['assets'])}")
    print(
        json.dumps(
            {
                "assets": len(manifest["assets"]),
                "archive_bytes": total_bytes,
                "categories": manifest["categories"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
