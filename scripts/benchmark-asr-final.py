"""Measure local streaming/final ASR load, VRAM and warm single-utterance latency."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf
import torch

from mindspace_graph.streaming_asr import ASRSessionOptions, FunASRRuntime


def _sync() -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _timed(operation):
    _sync()
    started = perf_counter()
    value = operation()
    _sync()
    return value, round((perf_counter() - started) * 1000, 1)


def _pcm(path: Path) -> tuple[bytes, int]:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    target_rate = 16000
    if sample_rate != target_rate:
        target_length = max(1, int(round(len(mono) * target_rate / sample_rate)))
        mono = np.interp(
            np.linspace(0, max(0, len(mono) - 1), target_length),
            np.arange(len(mono)),
            mono,
        ).astype("float32")
    pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, int(len(mono) / target_rate * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--trace-direct", action="store_true")
    args = parser.parse_args()

    runtime = FunASRRuntime(args.model_root.resolve(), device=args.device)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
    stream_ready, stream_load_ms = _timed(runtime.load)
    stream_reserved_mib = (
        round(torch.cuda.memory_reserved() / 1024**2, 1) if torch.cuda.is_available() else 0
    )
    final_ready, final_load_ms = _timed(runtime.load_refiner)
    combined_reserved_mib = (
        round(torch.cuda.memory_reserved() / 1024**2, 1) if torch.cuda.is_available() else 0
    )
    peak_reserved_mib = (
        round(torch.cuda.max_memory_reserved() / 1024**2, 1) if torch.cuda.is_available() else 0
    )

    pcm, audio_ms = _pcm(args.audio.resolve())
    if args.trace_direct:
        samples = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        print(
            runtime.final_asr.generate(
                input=[torch.from_numpy(samples)],
                batch_size=1,
                language="中文",
                itn=True,
                hotwords=["Mindspace", "FunASR"],
                max_length=192,
            )
        )
        return
    options = ASRSessionOptions(
        final_refinement_timeout_ms=5000,
        decoder_hotwords=("Mindspace", "FunASR"),
    )
    cold, cold_ms = _timed(
        lambda: runtime.refine_final_pcm(pcm, options, playback_active=False)
    )
    warm, warm_ms = _timed(
        lambda: runtime.refine_final_pcm(pcm, options, playback_active=False)
    )
    print(
        json.dumps(
            {
                "stream_ready": stream_ready,
                "final_ready": final_ready,
                "stream_load_ms": stream_load_ms,
                "final_load_ms": final_load_ms,
                "audio_ms": audio_ms,
                "cold_inference_ms": cold_ms,
                "warm_inference_ms": warm_ms,
                "stream_reserved_mib": stream_reserved_mib,
                "combined_reserved_mib": combined_reserved_mib,
                "peak_reserved_mib": peak_reserved_mib,
                "cold": cold,
                "warm": warm,
                "status": runtime.status(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
