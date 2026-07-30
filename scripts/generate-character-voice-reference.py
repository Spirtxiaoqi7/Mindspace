"""Generate stable Qwen3-TTS VoiceDesign references for Mindspace.

This utility is intentionally offline.  It creates a small set of candidate
reference WAV files that can later be converted into a reusable Base-model
voice-clone prompt.  No user profile or conversation data is read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


REFERENCE_TEXT = (
    "嗯……过来一点。呵……别急着解释，先让我听听你今天怎么了。"
    "呼……慢慢说。你说你的，我就在这里看着你。等你说累了，再靠近一点也不迟。"
)

VOICE_CANDIDATES = {
    "mature_gentle_alluring": (
        "一位二十八到三十二岁的成年女性，标准中文，音域偏中低，声线成熟温柔、"
        "松弛而有磁性。整体语速比日常交谈慢约一成，句间有自然停顿；开头能听见"
        "轻微换气，中段有一次短促低笑，转折前有舒缓呼气。像深夜与熟悉伴侣自然"
        "交谈，亲近时带轻微气息感和若有若无的魅惑；呼吸清晰但不过分夸张，不幼态、"
        "不甜腻、不播音、不激昂、不做舞台式表演。"
    ),
    "velvet_intimate": (
        "成年女性的中低音中文声线，温柔、从容、略带沙绒质感和轻微气息感。距离很近，"
        "语速自然偏慢，语尾柔和，有克制的吸引力；不要少女音、播音腔、热血感或戏剧化。"
    ),
    "warm_mature": (
        "成熟温暖的成年中文女声，中低音，清晰但不刻意，松弛、有耐心、有生活感。"
        "像和长期伴侣在家里低声聊天，偶尔含着一点笑意；不要卖萌、不要朗诵、不要强情绪。"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=739173)
    parser.add_argument(
        "--candidate",
        choices=tuple(VOICE_CANDIDATES),
        action="append",
        help="Generate only the selected candidate; repeat to select several.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    load_seconds = time.perf_counter() - started

    manifest: dict[str, object] = {
        "model": str(args.model),
        "seed": args.seed,
        "reference_text": REFERENCE_TEXT,
        "load_seconds": round(load_seconds, 3),
        "candidates": [],
    }
    selected = set(args.candidate or VOICE_CANDIDATES)
    for name, instruction in VOICE_CANDIDATES.items():
        if name not in selected:
            continue
        candidate_started = time.perf_counter()
        wavs, sample_rate = model.generate_voice_design(
            text=REFERENCE_TEXT,
            language="Chinese",
            instruct=instruction,
            non_streaming_mode=True,
            do_sample=True,
            temperature=0.75,
            top_p=0.9,
            max_new_tokens=1024,
        )
        output_path = args.output / f"{name}.wav"
        sf.write(output_path, wavs[0], sample_rate, subtype="PCM_16")
        manifest["candidates"].append(
            {
                "name": name,
                "instruction": instruction,
                "path": str(output_path),
                "sample_rate": sample_rate,
                "duration_seconds": round(len(wavs[0]) / sample_rate, 3),
                "generation_seconds": round(
                    time.perf_counter() - candidate_started, 3
                ),
            }
        )

    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(manifest_path)


if __name__ == "__main__":
    main()
