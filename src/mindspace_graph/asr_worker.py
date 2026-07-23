"""Dedicated FunASR process so model loading never blocks the LangGraph API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from mindspace_graph.streaming_asr import (
    ASRSessionOptions,
    ASRTextCorrector,
    FunASRRuntime,
    FunASRStreamSession,
    apply_final_refinement,
)
from mindspace_graph.version import APP_VERSION


def create_worker_app(model_root: Path, device: str) -> FastAPI:
    runtime = FunASRRuntime(model_root, device=device)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if os.environ.get("MINDSPACE_ASR_PRELOAD", "1") == "1":
            await asyncio.to_thread(runtime.load)
            if os.environ.get("MINDSPACE_ASR_FINAL_PRELOAD", "1") == "1":
                await asyncio.to_thread(runtime.load_refiner)
        yield

    app = FastAPI(title="Mindspace FunASR Worker", version=APP_VERSION, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        status = runtime.status()
        return {
            "ok": True,
            **status,
            "ready": runtime.asr is not None,
            "loaded": runtime.asr is not None,
            "emotion": {"enabled": False, "status": "disabled"},
        }

    @app.post("/emotion/results")
    async def emotion_results(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": False,
            "results": [],
            "pending": 0,
        }

    @app.post("/transcribe")
    async def transcribe_reference(request: Request) -> dict[str, Any]:
        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="参考音频为空")
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="参考音频不能超过 20 MiB")
        try:
            result = await asyncio.to_thread(runtime.transcribe_audio, content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.websocket("/ws")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"event": "asr.loading", "data": {"provider": "funasr"}})
        if not await asyncio.to_thread(runtime.load):
            await websocket.send_json(
                {"event": "asr.error", "data": {"error": runtime.error or "load failed"}}
            )
            await websocket.close(code=1011)
            return

        options = ASRSessionOptions()
        session = FunASRStreamSession(runtime, options)
        input_locked = False
        await websocket.send_json(
            {
                "event": "asr.ready",
                "data": {"provider": "funasr", "sample_rate": options.sample_rate},
            }
        )
        try:
            while True:
                message = await websocket.receive()
                events: list[dict[str, Any]] = []
                if message.get("bytes") is not None:
                    if not input_locked:
                        events = await asyncio.to_thread(session.feed, message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    action = control.get("action")
                    if action == "start":
                        input_locked = False
                        session.reset()
                        options.silence_ms = int(control.get("silence_ms") or 600)
                        threshold_db = float(control.get("energy_threshold_db") or -35)
                        options.energy_threshold = 10 ** (threshold_db / 20)
                        options.min_speech_ms = int(control.get("min_speech_ms") or 120)
                        options.auto_send = bool(control.get("auto_send", True))
                        options.candidate_release_ms = int(
                            control.get("candidate_release_ms") or 240
                        )
                        options.playback_active = bool(control.get("playback_active", False))
                        options.playback_text = str(control.get("playback_text") or "")[:4000]
                        options.deferred_during_playback = bool(
                            control.get("deferred_during_playback", True)
                        )
                        options.dynamic_endpointing = bool(
                            control.get("dynamic_endpointing", True)
                        )
                        options.final_refinement_enabled = bool(
                            control.get("final_refinement_enabled", True)
                        )
                        options.final_refinement_timeout_ms = int(
                            control.get("final_refinement_timeout_ms") or 1400
                        )
                        options.final_refinement_min_audio_ms = int(
                            control.get("final_refinement_min_audio_ms") or 320
                        )
                        options.final_refinement_max_audio_ms = int(
                            control.get("final_refinement_max_audio_ms") or 15000
                        )
                        vocabulary = control.get("vocabulary")
                        if isinstance(vocabulary, dict):
                            options.vocabulary_revision = str(vocabulary.get("revision") or "")
                            options.decoder_hotwords = tuple(
                                str(item)
                                for item in vocabulary.get("decoder_hotwords", [])
                                if str(item).strip()
                            )
                            options.explicit_corrections = {
                                str(key): str(value)
                                for key, value in dict(vocabulary.get("explicit") or {}).items()
                                if str(key).strip() and str(value).strip()
                            }
                            options.fuzzy_targets = tuple(
                                item
                                for item in vocabulary.get("fuzzy_targets", [])
                                if isinstance(item, dict)
                            )
                            session.corrector = ASRTextCorrector(options)
                    elif action == "playback_state":
                        session.configure_playback(
                            playing=bool(control.get("playing", False)),
                            energy_threshold=10
                            ** (float(control.get("energy_threshold_db") or -35) / 20),
                            min_speech_ms=int(control.get("min_speech_ms") or 120),
                            candidate_release_ms=int(
                                control.get("candidate_release_ms") or 240
                            ),
                            playback_text=str(control.get("playback_text") or ""),
                        )
                    elif action == "input_gate":
                        input_locked = bool(control.get("locked", False))
                        session.configure_input_gate(input_locked)
                        await websocket.send_json(
                            {
                                "event": "asr.input_gate",
                                "data": {
                                    "locked": input_locked,
                                    "reason": str(control.get("reason") or ""),
                                },
                            }
                        )
                    elif action == "cancel":
                        session.reset()
                        await websocket.send_json({"event": "asr.cancelled", "data": {}})
                    elif action == "stop":
                        silence = b"\x00\x00" * int(options.sample_rate * 0.5)
                        events = await asyncio.to_thread(session.feed, silence, force_final=True)
                for event in events:
                    if event.get("event") in {"asr.final", "asr.deferred"}:
                        pcm, playback_active = session.pop_finalized_audio()
                        if pcm:
                            refinement = await asyncio.to_thread(
                                runtime.refine_final_pcm,
                                pcm,
                                options,
                                playback_active=playback_active,
                            )
                            apply_final_refinement(event, refinement, session.corrector)
                    await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            session.reset()

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Mindspace FunASR streaming worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8766, type=int)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path.cwd() / "assets" / "models" / "asr",
    )
    parser.add_argument("--device", default=os.environ.get("MINDSPACE_ASR_DEVICE", "cuda:0"))
    args = parser.parse_args()
    app = create_worker_app(args.model_root.resolve(), args.device)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
