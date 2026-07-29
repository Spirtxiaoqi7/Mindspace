"""Dedicated FunASR process so model loading never blocks the LangGraph API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from array import array
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from mindspace_graph.native_microphone import NativeMicrophoneCapture
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
    native_capture = NativeMicrophoneCapture()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        capture_start = asyncio.create_task(asyncio.to_thread(native_capture.start))
        capture_supervisor: asyncio.Task[None] | None = None
        if os.environ.get("MINDSPACE_ASR_PRELOAD", "1") == "1":
            await asyncio.to_thread(runtime.load)
            if os.environ.get("MINDSPACE_ASR_FINAL_PRELOAD", "1") == "1":
                await asyncio.to_thread(runtime.load_refiner)
        await capture_start
        if native_capture.status()["available"]:
            async def supervise_capture() -> None:
                while True:
                    await asyncio.sleep(1)
                    await asyncio.to_thread(native_capture.restart_if_stalled)

            capture_supervisor = asyncio.create_task(supervise_capture())
        try:
            yield
        finally:
            if capture_supervisor is not None:
                capture_supervisor.cancel()
                await asyncio.gather(capture_supervisor, return_exceptions=True)
            await asyncio.to_thread(native_capture.stop)

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
            "native_capture": native_capture.status(),
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
        intent_id = ""
        generation = 0
        event_seq = 0

        async def emit(event: str, data: dict[str, Any] | None = None) -> None:
            """Envelope every event so an old transport cannot mutate a new turn."""

            nonlocal event_seq
            event_seq += 1
            await websocket.send_json(
                {
                    "event": event,
                    "data": {
                        **(data or {}),
                        "intent_id": intent_id,
                        "generation": generation,
                        "event_seq": event_seq,
                    },
                }
            )

        await emit("asr.loading", {"provider": "funasr"})
        if not await asyncio.to_thread(runtime.load):
            await emit("asr.error", {"error": runtime.error or "load failed"})
            await websocket.close(code=1011)
            return

        options = ASRSessionOptions()
        session = FunASRStreamSession(runtime, options)
        input_locked = False
        session_active = False
        loop = asyncio.get_running_loop()
        native_queue = native_capture.subscribe(loop)
        capture_status = native_capture.status()
        native_enabled = native_queue is not None
        await emit(
            "asr.ready",
            {
                "provider": "funasr",
                "sample_rate": options.sample_rate,
                "capture_mode": "native" if native_enabled else "browser",
                "capture_ready": bool(capture_status["ready"]) if native_enabled else True,
                "capture_state": capture_status.get("capture_state", capture_status.get("state", "")),
                "capture_endpoint": capture_status.get("capture_endpoint", {}),
                "first_pcm_ms": capture_status.get("first_pcm_ms", 0),
            },
        )

        async def send_events(events: list[dict[str, Any]]) -> None:
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
                await emit(str(event.get("event") or "asr.unknown"), dict(event.get("data") or {}))

        def pcm_level(pcm: bytes) -> float:
            samples = array("h")
            samples.frombytes(pcm)
            if not samples:
                return 0.0
            mean_square = sum(sample * sample for sample in samples) / len(samples)
            return min(1.0, (mean_square**0.5) / 12_000)

        receive_task: asyncio.Task[dict[str, Any]] | None = asyncio.create_task(
            websocket.receive()
        )
        pcm_task: asyncio.Task[bytes] | None = (
            asyncio.create_task(native_queue.get()) if native_queue is not None else None
        )
        capture_announced = bool(capture_status["ready"]) if native_enabled else True
        last_level_at = 0.0
        try:
            while True:
                pending_tasks = {
                    task for task in (receive_task, pcm_task) if task is not None
                }
                done, _pending = await asyncio.wait(
                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                events: list[dict[str, Any]] = []
                if pcm_task is not None and pcm_task in done:
                    pcm = pcm_task.result()
                    pcm_task = asyncio.create_task(native_queue.get()) if native_queue else None
                    if not capture_announced:
                        capture_announced = True
                        await emit(
                            "asr.capture_ready",
                            {
                                "capture_mode": "native",
                                **native_capture.status(),
                            },
                        )
                    now = time.monotonic()
                    if now - last_level_at >= 0.1:
                        last_level_at = now
                        await emit("asr.level", {"level": pcm_level(pcm)})
                    if session_active and not input_locked:
                        events.extend(await asyncio.to_thread(session.feed, pcm))

                if receive_task is not None and receive_task in done:
                    message = receive_task.result()
                    if message.get("type") == "websocket.disconnect":
                        break
                    receive_task = asyncio.create_task(websocket.receive())
                    if message.get("bytes") is not None:
                        if not native_enabled and session_active and not input_locked:
                            events.extend(
                                await asyncio.to_thread(session.feed, message["bytes"])
                            )
                    elif message.get("text"):
                        control = json.loads(message["text"])
                        action = control.get("action")
                        if action == "start":
                            intent_id = str(control.get("intent_id") or "")
                            generation = int(control.get("generation") or 0)
                            event_seq = 0
                            input_locked = False
                            session_active = True
                            session.reset()
                            options.silence_ms = int(control.get("silence_ms") or 600)
                            threshold_db = float(
                                control.get("energy_threshold_db") or -35
                            )
                            options.energy_threshold = 10 ** (threshold_db / 20)
                            options.min_speech_ms = int(
                                control.get("min_speech_ms") or 120
                            )
                            options.auto_send = bool(control.get("auto_send", True))
                            options.candidate_release_ms = int(
                                control.get("candidate_release_ms") or 240
                            )
                            options.playback_active = bool(
                                control.get("playback_active", False)
                            )
                            options.playback_text = str(
                                control.get("playback_text") or ""
                            )[:4000]
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
                                options.vocabulary_revision = str(
                                    vocabulary.get("revision") or ""
                                )
                                options.decoder_hotwords = tuple(
                                    str(item)
                                    for item in vocabulary.get("decoder_hotwords", [])
                                    if str(item).strip()
                                )
                                options.explicit_corrections = {
                                    str(key): str(value)
                                    for key, value in dict(
                                        vocabulary.get("explicit") or {}
                                    ).items()
                                    if str(key).strip() and str(value).strip()
                                }
                                options.fuzzy_targets = tuple(
                                    item
                                    for item in vocabulary.get("fuzzy_targets", [])
                                    if isinstance(item, dict)
                                )
                                session.corrector = ASRTextCorrector(options)
                            await emit(
                                "asr.activated",
                                {
                                    "capture_mode": "native" if native_enabled else "browser",
                                    "capture_ready": bool(native_capture.status()["ready"]) if native_enabled else True,
                                },
                            )
                        elif action == "playback_state":
                            session.configure_playback(
                                playing=bool(control.get("playing", False)),
                                energy_threshold=10
                                ** (
                                    float(
                                        control.get("energy_threshold_db") or -35
                                    )
                                    / 20
                                ),
                                min_speech_ms=int(
                                    control.get("min_speech_ms") or 120
                                ),
                                candidate_release_ms=int(
                                    control.get("candidate_release_ms") or 240
                                ),
                                playback_text=str(
                                    control.get("playback_text") or ""
                                ),
                            )
                        elif action == "input_gate":
                            input_locked = bool(control.get("locked", False))
                            session.configure_input_gate(input_locked)
                            await emit(
                                "asr.input_gate",
                                {
                                    "locked": input_locked,
                                    "reason": str(control.get("reason") or ""),
                                },
                            )
                        elif action in {"cancel", "deactivate"}:
                            session_active = False
                            session.reset()
                            await emit("asr.deactivated", {})
                        elif action == "stop":
                            session_active = False
                            silence = b"\x00\x00" * int(options.sample_rate * 0.5)
                            events.extend(
                                await asyncio.to_thread(
                                    session.feed, silence, force_final=True
                                )
                            )
                await send_events(events)
        except (WebSocketDisconnect, RuntimeError):
            session.reset()
        finally:
            for task in (receive_task, pcm_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (receive_task, pcm_task) if task is not None),
                return_exceptions=True,
            )
            if native_queue is not None:
                native_capture.unsubscribe(loop, native_queue)
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
