"""Convert one approved reference WAV into a reusable Qwen3-TTS Base profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np
from qwen_tts import Qwen3TTSModel
from safetensors.torch import save_file
import soundfile as sf
import torch


DEFAULT_VOICE_NAME = "mindspace_mature_alluring"
VALIDATION_TEXTS = (
    "嗯……我刚把窗帘拉开一点。你慢慢说，我在听。",
    "呵……别急，先坐近一点。呼……今天剩下的时间，我想好好陪着你。",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voice-name", default=DEFAULT_VOICE_NAME)
    parser.add_argument("--preview-output", type=Path)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def speaker_embedding(
    model: Qwen3TTSModel,
    waveform: np.ndarray,
    sample_rate: int,
) -> torch.Tensor:
    target_rate = model.model.speaker_encoder_sample_rate
    audio = waveform.astype(np.float32)
    if sample_rate != target_rate:
        audio = librosa.resample(
            y=audio,
            orig_sr=sample_rate,
            target_sr=target_rate,
        )
    return model.model.extract_speaker_embedding(
        audio=audio,
        sr=target_rate,
    ).detach().cpu().reshape(-1)


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    model = Qwen3TTSModel.from_pretrained(
        str(args.model),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    prompt = model.create_voice_clone_prompt(
        ref_audio=str(args.reference),
        ref_text=args.reference_text,
        x_vector_only_mode=False,
    )[0]
    if prompt.ref_code is None:
        raise RuntimeError("ICL voice profile did not produce reference codec tokens")

    safe_name = args.voice_name.strip().lower()
    tensor_name = f"{safe_name}.safetensors"
    tensor_path = args.output / tensor_name
    save_file(
        {
            "speaker_embedding": prompt.ref_spk_embedding.detach().cpu().contiguous(),
            "ref_code": prompt.ref_code.detach().cpu().contiguous(),
        },
        tensor_path,
    )
    manifest = {
        "schema_version": 1,
        "model_type": "qwen3_tts",
        "default_voice": safe_name,
        "selection_policy": {
            "mature": 0.4,
            "gentle": 0.35,
            "alluring": 0.25,
        },
        "voices": {
            safe_name: {
                "name": safe_name,
                "file": tensor_name,
                "mode": "icl",
                "ref_text": args.reference_text,
                "description": "成熟、温柔、轻微魅惑，带自然换气、轻笑与慢停顿的成年中文女性声线",
                "priority_weight": 1.0,
                "reference_sha256": sha256_file(args.reference),
            }
        },
    }
    if args.preview_output is not None:
        args.preview_output.mkdir(parents=True, exist_ok=True)
        preview_embeddings: list[torch.Tensor] = []
        preview_files: list[str] = []
        torch.manual_seed(739173)
        for index, text in enumerate(VALIDATION_TEXTS, start=1):
            wavs, sample_rate = model.generate_voice_clone(
                text=text,
                language="Chinese",
                voice_clone_prompt=[prompt],
                non_streaming_mode=True,
                do_sample=True,
                temperature=0.78,
                top_p=0.92,
                max_new_tokens=1024,
            )
            preview_path = args.preview_output / f"base-consistency-{index}.wav"
            sf.write(preview_path, wavs[0], sample_rate, subtype="PCM_16")
            preview_files.append(str(preview_path))
            preview_embeddings.append(
                speaker_embedding(model, wavs[0], sample_rate)
            )
        similarity = torch.nn.functional.cosine_similarity(
            preview_embeddings[0],
            preview_embeddings[1],
            dim=0,
        )
        manifest["validation"] = {
            "texts": list(VALIDATION_TEXTS),
            "files": preview_files,
            "speaker_embedding_cosine": round(float(similarity), 6),
        }
    manifest_path = args.output / "custom_voice_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(manifest_path)


if __name__ == "__main__":
    main()
