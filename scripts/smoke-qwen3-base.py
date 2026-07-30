"""Exercise Mindspace's Qwen3 Base provider and write playable WAV artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
import wave

from mindspace_graph.audio import AudioService
from mindspace_graph.settings import AppSettings


SAMPLES = (
    "嗯，我刚把窗帘拉开一点。你慢慢说，我在听。",
    "别急，先坐近一点。今天剩下的时间，我想好好陪着你。",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(
        runtime_dir=args.output / "runtime",
        tts_provider="qwen3-vllm",
        tts_qwen3_vllm_url="http://127.0.0.1:8091",
        tts_qwen3_vllm_model="mindspace-qwen3-tts",
        tts_qwen3_vllm_voice="mindspace_mature_alluring",
        tts_qwen3_vllm_task_type="Base",
        tts_qwen3_vllm_language="Chinese",
    )
    service = AudioService(settings)
    metrics: list[dict[str, object]] = []
    try:
        for index, text in enumerate(SAMPLES, start=1):
            started = time.perf_counter()
            stream, sample_rate = await service.stream_synthesize(
                text,
                request_id=f"base-smoke-{index}",
            )
            first_pcm_ms: int | None = None
            chunks: list[bytes] = []
            async for chunk in stream:
                if first_pcm_ms is None:
                    first_pcm_ms = round((time.perf_counter() - started) * 1000)
                chunks.append(chunk)
            pcm = b"".join(chunks)
            wav_path = args.output / f"qwen3-base-smoke-{index}.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm)
            metrics.append(
                {
                    "text": text,
                    "path": str(wav_path),
                    "first_pcm_ms": first_pcm_ms,
                    "completed_ms": round((time.perf_counter() - started) * 1000),
                    "pcm_bytes": len(pcm),
                    "duration_seconds": round(len(pcm) / 2 / sample_rate, 3),
                }
            )
    finally:
        await service.aclose()
    metrics_path = args.output / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(metrics_path)


if __name__ == "__main__":
    asyncio.run(main())
