"""Audit the curated GPT-SoVITS voice archives without downloading whole ZIP files.

The ModelScope archives use legacy GBK ZIP filenames.  This script reads only
the remote central directory, decodes those names deterministically, and emits
the runtime catalog consumed by both the Launcher and Mindspace Core.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPOSITORY = "aihobbyist/GPT-SoVITS_Model_Collection"
RESOLVE_BASE = f"https://www.modelscope.cn/models/{REPOSITORY}/resolve"
FILES_API = f"https://www.modelscope.cn/api/v1/models/{REPOSITORY}/repo/files"

REQUESTED = {
    "原神": [
        ("八重神子", "v4-yae-miko"),
        ("雷电将军", "v4-raiden-shogun"),
        ("丽莎", "v4-lisa"),
        ("夜兰", "v4-yelan"),
        ("申鹤", "v4-shenhe"),
        ("闲云", "v4-xianyun"),
        ("芙宁娜", "v4-furina"),
        ("阿蕾奇诺", "v4-arlecchino"),
        ("玛薇卡", "v4-mavuika"),
        ("甘雨", "v4-ganyu"),
        ("凝光", "v4-ningguang"),
        ("北斗", "v4-beidou"),
    ],
    "星穹铁道": [
        ("卡芙卡", "v2proplus-kafka"),
        ("姬子", "v2proplus-himeko-hsr"),
        ("黑天鹅", "v2proplus-black-swan"),
        ("黄泉", "v2proplus-acheron"),
        ("镜流", "v2proplus-jingliu"),
        ("阮梅", "v2proplus-ruan-mei"),
        ("翡翠", "v2proplus-jade"),
        ("大黑塔", "v2proplus-the-herta"),
        ("花火", "v2proplus-sparkle"),
        ("知更鸟", "v2proplus-robin"),
    ],
    "鸣潮": [
        ("长离", "v4-changli"),
        ("守岸人", "v4-shorekeeper"),
        ("吟霖", "v4-yinlin"),
        ("坎特蕾拉", "v4-cantarella"),
        ("今汐", "v4-jinhsi"),
        ("椿", "v4-camellya"),
        ("弗洛洛", "v4-phrolova"),
        ("珂莱塔", "v4-carlotta"),
        ("菲比", "v4-phoebe"),
        ("赞妮", "v4-zani"),
    ],
    "绝区零": [
        ("伊芙琳", "v4-evelyn"),
        ("丽娜", "v4-rina"),
        ("简", "v4-jane-doe"),
        ("雅", "v4-miyabi"),
        ("朱鸢", "v4-zhu-yuan"),
        ("耀嘉音", "v4-astra-yao"),
        ("薇薇安", "v4-vivian"),
        ("柳", "v4-yanagi"),
    ],
    "崩坏三": [
        ("爱莉希雅", "v4-elysia-2026"),
        ("妖精爱莉", "v4-elf-elysia"),
        ("伊甸", "v4-eden"),
        ("梅比乌斯", "v4-mobius"),
        ("阿波尼亚", "v4-aponia"),
        ("丽塔", "v4-rita"),
        ("姬子", "v4-himeko-hi3"),
        ("八重樱", "v4-sakura-hi3"),
    ],
}

DISPLAY_FRANCHISE = {"星穹铁道": "崩铁"}


class HttpRangeReader(io.RawIOBase):
    def __init__(self, url: str, size: int, chunk_size: int = 256 * 1024) -> None:
        self.url = url
        self.size = size
        self.chunk_size = chunk_size
        self.position = 0
        self.cache: dict[int, bytes] = {}

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self.position = offset
        elif whence == io.SEEK_CUR:
            self.position += offset
        elif whence == io.SEEK_END:
            self.position = self.size + offset
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.position >= self.size:
            return b""
        remaining = self.size - self.position if size < 0 else min(size, self.size - self.position)
        output = bytearray()
        while remaining:
            index = self.position // self.chunk_size
            if index not in self.cache:
                start = index * self.chunk_size
                end = min(self.size - 1, start + self.chunk_size - 1)
                request = urllib.request.Request(
                    self.url,
                    headers={
                        "Range": f"bytes={start}-{end}",
                        "User-Agent": "Mindspace-Voice-Audit/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    self.cache[index] = response.read()
            block = self.cache[index]
            offset = self.position - index * self.chunk_size
            take = min(remaining, len(block) - offset)
            if take <= 0:
                raise OSError("remote range response ended early")
            output.extend(block[offset : offset + take])
            self.position += take
            remaining -= take
        return bytes(output)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mindspace-Voice-Audit/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def decode_zip_name(name: str) -> str:
    try:
        return name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def normal_name(name: str) -> str:
    return re.sub(r"_(?:ZH|CN)$", "", name.removesuffix(".zip"), flags=re.IGNORECASE).replace(
        "•", ""
    )


def inspect_archive(url: str, size: int) -> dict[str, str]:
    with zipfile.ZipFile(HttpRangeReader(url, size)) as archive:
        names = [decode_zip_name(item.filename) for item in archive.infolist() if not item.is_dir()]
    gpt = next(name for name in names if name.lower().endswith(".ckpt"))
    sovits = next(name for name in names if name.lower().endswith(".pth"))
    references = [
        name for name in names if name.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".ogg"))
    ]
    reference = next((name for name in references if "【默认】" in name), references[0])
    root = "/".join(gpt.split("/")[:2])
    family = root.split("/", 1)[0]
    reference_text = re.sub(r"^【[^】]+】", "", Path(reference).stem)
    return {
        "family": family,
        "archive_root": root,
        "gpt_weight": Path(gpt).name,
        "sovits_weight": Path(sovits).name,
        "reference_text": reference_text,
    }


def build_catalog() -> dict:
    voices = []
    for game, requested in REQUESTED.items():
        root = f"{game}/中文"
        query = urllib.parse.urlencode({"Revision": "master", "Root": root})
        files = fetch_json(f"{FILES_API}?{query}")["Data"]["Files"]
        archives = [
            item
            for item in files
            if item.get("Type") == "blob" and item["Name"].lower().endswith(".zip")
        ]
        for character, voice_id in requested:
            target = character.replace("•", "")
            match = next((item for item in archives if normal_name(item["Name"]) == target), None)
            if match is None:
                raise RuntimeError(f"voice archive is missing: {game}/{character}")
            revision = str(match["Revision"])
            remote_path = str(match["Path"])
            url = f"{RESOLVE_BASE}/{revision}/{urllib.parse.quote(remote_path, safe='/')}"
            inspected = inspect_archive(url, int(match["Size"]))
            family = inspected["family"]
            release_year = dt.datetime.fromtimestamp(
                int(match["CommittedDate"]), tz=dt.UTC
            ).year
            voice = {
                "id": voice_id,
                "label": f"{family.upper() if family == 'v4' else family}-{character}",
                "character": character,
                "franchise": DISPLAY_FRANCHISE.get(game, game),
                "family": family,
                "release_year": release_year,
                "sample_rate": 48_000 if family == "v4" else 32_000,
                "component_id": f"gpt-sovits-{voice_id}",
                "directory": f"tts/gpt-sovits/runtime/voices/{voice_id}",
                "gpt_weight": inspected["gpt_weight"],
                "sovits_weight": inspected["sovits_weight"],
                "reference_audio": "reference.wav",
                "reference_text": inspected["reference_text"],
                "reference_language": "zh",
                "prosody": {
                    "top_k": 20 if family == "v4" else 15,
                    "top_p": 0.6 if family == "v4" else 0.8,
                    "temperature": 0.6 if family == "v4" else 0.8,
                    "text_split_method": "cut5",
                    "fragment_interval": 0.24,
                },
                "download": {
                    "type": "zip",
                    "path": remote_path,
                    "revision": revision,
                    "size": int(match["Size"]),
                    "sha256": str(match["Sha256"]),
                    "archive_root": inspected["archive_root"],
                },
            }
            voices.append(voice)

    old_elysia = next(item for item in voices if item["id"] == "v4-elysia-2026")
    old_download = old_elysia["download"]
    old_elysia.update(
        {
            "label": "V4-爱莉希雅（2026）",
            "release_year": 2026,
            "gpt_weight": "../../GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "sovits_weight": "elysia_v4_hq_20260315_e120_s6360_l32.pth",
            "download": {
                "type": "lora-with-reference",
                "size": 69_659_823 + int(old_download["size"]),
                "lora": {
                    "url": "https://huggingface.co/AyerElysia/elysia-gpt-sovits-lora-v4/resolve/main/upload.tar.gz",
                    "size": 69_659_823,
                    "sha256": "e1c20121c09961fdfdaa90db050eb91ac061bdac13f44c8fab5ee16fcdc78472",
                },
                "reference": old_download,
            },
        }
    )
    voices.sort(
        key=lambda item: (
            -int(item["release_year"]),
            item["family"] != "v4",
            item["franchise"],
            item["character"],
        )
    )
    return {"schema_version": "1.1.0", "voices": voices}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    catalog = build_catalog()
    text = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
