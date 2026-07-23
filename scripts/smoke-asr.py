"""Stream the bundled example WAV through the real FunASR WebSocket worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

from websockets.asyncio.client import connect


async def run(url: str, audio: Path, run_id: str = "") -> tuple[dict, bool]:
    async with connect(url, max_size=8 * 1024 * 1024, open_timeout=10) as websocket:
        while True:
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=300))
            print(json.dumps(event, ensure_ascii=False), flush=True)
            if event.get("event") == "asr.error":
                raise RuntimeError(str(event.get("data", {}).get("error") or "ASR load failed"))
            if event.get("event") == "asr.ready":
                break
        await websocket.send(
            json.dumps({"action": "start", "auto_send": True, "run_id": run_id})
        )
        interrupted = False
        with wave.open(str(audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (
                1,
                2,
                16000,
            ):
                raise RuntimeError("smoke WAV must be mono PCM16 at 16 kHz")
            while chunk := source.readframes(7680):
                await websocket.send(chunk)
                await asyncio.sleep(0.02)
        await websocket.send(json.dumps({"action": "stop"}))
        while True:
            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=60))
            print(json.dumps(event, ensure_ascii=False), flush=True)
            if event.get("event") == "asr.interrupted":
                interrupted = True
            if event.get("event") == "asr.error":
                raise RuntimeError(str(event.get("data", {}).get("error") or "ASR failed"))
            if event.get("event") == "asr.final":
                text = str(event.get("data", {}).get("text") or "").strip()
                if not text:
                    raise RuntimeError("ASR returned an empty final transcript")
                return event, interrupted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8766/ws")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--expect-interrupt", action="store_true")
    parser.add_argument(
        "--audio",
        type=Path,
        default=Path("assets/models/asr/paraformer-zh-streaming/example/asr_example.wav"),
    )
    args = parser.parse_args()
    result, interrupted = asyncio.run(run(args.url, args.audio.resolve(), args.run_id))
    if args.expect_interrupt and not interrupted:
        raise RuntimeError("ASR proxy did not emit the expected interruption event")
    print(f"ASR_SMOKE_TEXT={result['data']['text']}")


if __name__ == "__main__":
    main()
