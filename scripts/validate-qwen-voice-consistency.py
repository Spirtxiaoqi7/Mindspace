"""Measure whether Qwen TTS samples keep the same speaker identity.

This diagnostic intentionally loads Qwen3-TTS Base only for its speaker
encoder.  Product synthesis can still use CustomVoice; no model weights are
trained or changed here.
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import librosa
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder-model", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output", type=Path)
    parser.add_argument("samples", nargs="+", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if len(args.samples) < 2:
        raise SystemExit("at least two WAV samples are required")

    model = Qwen3TTSModel.from_pretrained(
        str(args.encoder_model),
        device_map=args.device,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    target_rate = model.model.speaker_encoder_sample_rate
    embeddings: dict[str, torch.Tensor] = {}
    for sample in args.samples:
        waveform, sample_rate = sf.read(sample, dtype="float32")
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)
        if sample_rate != target_rate:
            waveform = librosa.resample(
                y=waveform,
                orig_sr=sample_rate,
                target_sr=target_rate,
            )
        embeddings[sample.name] = (
            model.model.extract_speaker_embedding(
                audio=waveform,
                sr=target_rate,
            )
            .detach()
            .cpu()
            .reshape(-1)
        )

    pairs = []
    for left, right in combinations(embeddings, 2):
        cosine = torch.nn.functional.cosine_similarity(
            embeddings[left],
            embeddings[right],
            dim=0,
        )
        pairs.append(
            {
                "left": left,
                "right": right,
                "speaker_embedding_cosine": round(float(cosine), 6),
            }
        )
    result = {
        "encoder": str(args.encoder_model),
        "sample_count": len(embeddings),
        "minimum_speaker_embedding_cosine": min(
            item["speaker_embedding_cosine"] for item in pairs
        ),
        "pairs": pairs,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
