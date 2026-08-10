"""Audio, ASR, TTS, scene, and shared activity routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from mindspace_graph.audio import AudioProviderUnavailable
from mindspace_graph.gpt_sovits import public_voice_catalog, voice_definition
from mindspace_graph.shared_chapters import ActivityAction, ActivityStart, SceneSelectionUpdate
from mindspace_graph.streaming_asr import ASRSessionOptions, FunASRStreamSession, apply_final_refinement

from .context import (
    ApiContext,
    ASRCorrectionRequest,
    ASRVocabularyTestRequest,
    ASRVocabularyUpdateRequest,
    TTSRequest,
    _avatar_suffix,
    _voice_energy_threshold_db,
)


def register_routes(app: FastAPI, context: ApiContext) -> None:
    """Register this domain on the app-owned router surface."""
    container = context.container
    settings = context.settings
    audio = context.audio
    scene_asset_root = context.scene_asset_root

    @app.get("/api/v1/audio/asr/vocabulary")
    async def get_asr_vocabulary():
        return container.asr_vocabulary.snapshot()

    @app.put("/api/v1/audio/asr/vocabulary")
    async def put_asr_vocabulary(payload: ASRVocabularyUpdateRequest):
        try:
            return container.asr_vocabulary.replace_manual(payload.entries)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/audio/asr/vocabulary/test")
    async def test_asr_vocabulary(payload: ASRVocabularyTestRequest):
        return container.asr_vocabulary.test_text(payload.text)

    @app.post("/api/v1/audio/asr/corrections")
    async def add_asr_correction(payload: ASRCorrectionRequest):
        try:
            return container.asr_vocabulary.record_correction(payload.raw_text, payload.corrected_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/audio/asr/corrections")
    async def get_asr_correction_history(limit: int = 100):
        return {"items": container.asr_vocabulary.correction_history(limit=limit)}

    @app.get("/api/v1/audio/tts/voices")
    async def list_tts_voices():
        return public_voice_catalog(settings.model_root, settings.tts_gpt_sovits_voice)

    @app.get("/api/v1/audio/tts/qwen3/voices")
    async def list_qwen3_tts_voices():
        try:
            return await audio.qwen3_vllm_voices()
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/audio/tts/voice/select")
    async def select_tts_voice(payload: dict[str, Any]):
        voice_id = str(payload.get("voice_id") or "").strip()
        try:
            voice = voice_definition(voice_id)
            result = container.config.update(
                {"audio": {"tts_provider": "gpt-sovits", "tts_gpt_sovits_voice": voice_id}}
            )
            switched = await audio.select_gpt_sovits_voice(voice_id)
            return {
                "ok": True,
                "voice": voice,
                "worker": switched,
                "settings": result["audio"],
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AudioProviderUnavailable as exc:
            return {
                "ok": True,
                "pending_worker": True,
                "message": str(exc),
                "voice": voice,
                "settings": result["audio"],
            }

    @app.get("/api/v1/scenes")
    async def list_scenes():
        items = container.chapters.scenes()
        return {"items": items, "count": len(items)}

    @app.post("/api/v1/scenes/custom")
    async def upload_custom_scene(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
    ):
        data = await file.read()
        if not data or len(data) > 12 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="背景图片必须在 1 字节到 12 MiB 之间")
        try:
            suffix = _avatar_suffix(file.filename or "scene.webp", data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="背景仅支持 PNG、JPEG、WebP") from exc
        if suffix == ".gif":
            raise HTTPException(status_code=422, detail="背景仅支持 PNG、JPEG、WebP")
        token = uuid4().hex
        scene_id = f"custom_{token}"
        filename = f"scene-{token}{suffix}"
        target = scene_asset_root / filename
        temporary = target.with_name(f".{filename}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
            return container.chapters.create_custom_scene(
                scene_id=scene_id,
                title=title or Path(file.filename or "").stem,
                description=description,
                asset_id=target.stem,
                asset_url=f"/api/v1/scene/files/{filename}",
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise

    @app.get("/api/v1/sessions/{session_id}/scene")
    async def get_session_scene(session_id: str):
        try:
            return container.chapters.get_session_scene(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            status = 410 if "archived" in message else 409
            raise HTTPException(status_code=status, detail=message) from exc

    @app.put("/api/v1/sessions/{session_id}/scene")
    async def set_session_scene(session_id: str, payload: SceneSelectionUpdate):
        try:
            return container.chapters.set_session_scene(session_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            status = 409 if "revision conflict" in message else 422
            raise HTTPException(status_code=status, detail=message) from exc

    @app.get("/api/v1/activities")
    async def list_activities():
        return {"items": container.chapters.activities()}

    @app.get("/api/v1/characters/{character_id}/activity-sessions")
    async def list_activity_sessions(character_id: str, include_finished: bool = Query(default=True)):
        try:
            items = container.chapters.list_activity_sessions(character_id, include_finished=include_finished)
            return {"items": items, "count": len(items)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/activities/{activity_id}/sessions")
    async def start_activity(activity_id: str, payload: ActivityStart):
        try:
            return container.chapters.start_activity(activity_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/activity-sessions/{activity_session_id}")
    async def get_activity_session(activity_session_id: str):
        try:
            return container.chapters.get_activity_session(activity_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/activity-sessions/{activity_session_id}/actions")
    async def apply_activity_action(activity_session_id: str, payload: ActivityAction):
        try:
            return container.chapters.apply_activity_action(activity_session_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/audio/status")
    async def audio_status():
        return await audio.status()

    @app.get("/api/v1/audio/diagnostics")
    async def audio_diagnostics():
        """Return operational voice state without audio, transcript, or credentials."""

        status = await audio.status()
        asr_detail = status.get("asr_detail")
        native = asr_detail.get("native_capture", {}) if isinstance(asr_detail, dict) else {}
        return {
            "asr_ready": bool(status.get("asr_ready")),
            "tts_ready": bool(status.get("tts_ready")),
            "capture": {
                key: native.get(key)
                for key in (
                    "capture_state",
                    "capture_endpoint",
                    "device_name",
                    "source_sample_rate",
                    "sample_rate",
                    "first_pcm_ms",
                    "last_pcm_age_ms",
                    "subscribers",
                    "restart_count",
                    "last_restart_reason",
                    "error_code",
                    "error",
                )
            },
            "tts_queue": status.get("tts_queue", {}),
            "tts": {
                "provider": status.get("tts_provider"),
                "detail": status.get("tts_detail", {}),
                "metrics": status.get("tts_metrics", {}),
            },
        }

    @app.post("/api/v1/audio/tts")
    async def synthesize(payload: TTSRequest):
        try:
            path = await audio.synthesize(
                payload.text,
                request_id=payload.request_id,
                speed=payload.speed,
                voice_cue=payload.voice_cue,
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=path.name,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/v1/audio/tts/stream")
    async def stream_synthesize(payload: TTSRequest):
        try:
            stream, sample_rate = await audio.stream_synthesize(
                payload.text,
                request_id=payload.request_id,
                speed=payload.speed,
                voice_cue=payload.voice_cue,
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return StreamingResponse(
            stream,
            media_type="application/octet-stream",
            headers={
                "X-Audio-Format": "pcm_s16le",
                "X-Audio-Sample-Rate": str(sample_rate),
                "X-Audio-Channels": "1",
                "X-TTS-Provider": settings.tts_provider,
                "X-TTS-Text-Mode": ("full-response" if settings.tts_provider == "qwen3-vllm" else "streamed-segments"),
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/audio/tts/reference")
    async def upload_tts_reference(
        file: Annotated[UploadFile, File()],
        transcript: Annotated[str | None, Form()] = None,
    ):
        content = await file.read()
        if not content or len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="invalid reference audio")
        suffix = Path(file.filename or "reference.wav").suffix.lower()
        if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
            raise HTTPException(status_code=422, detail="unsupported audio format")
        previous = str(settings.tts_reference_audio or "")
        path = settings.runtime_dir / "data" / "audio" / f"reference-{uuid4().hex}{suffix}"
        path.write_bytes(content)
        audio_patch: dict[str, Any] = {"tts_reference_audio": str(path)}
        if transcript is not None:
            audio_patch["tts_reference_text"] = transcript.strip()
        try:
            result = container.config.update({"audio": audio_patch})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if previous:
            candidate = Path(previous)
            audio_root = (settings.runtime_dir / "data" / "audio").resolve()
            try:
                if candidate.resolve().is_relative_to(audio_root) and candidate != path:
                    candidate.unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "success": True,
            "reference": {
                "filename": file.filename or path.name,
                "stored_name": path.name,
                "format": suffix.removeprefix("."),
                "size": len(content),
                "configured": True,
                "transcript": str(result["audio"].get("tts_reference_text") or ""),
            },
            "settings": result["audio"],
        }

    @app.delete("/api/v1/audio/tts/reference")
    async def clear_tts_reference():
        current = str(settings.tts_reference_audio or "")
        result = container.config.update({"audio": {"tts_reference_audio": "", "tts_reference_text": ""}})
        if current:
            candidate = Path(current)
            audio_root = (settings.runtime_dir / "data" / "audio").resolve()
            try:
                if candidate.resolve().is_relative_to(audio_root) and candidate.is_file():
                    candidate.unlink()
            except OSError:
                pass
        return {"success": True, "reference": {"configured": False}, "settings": result["audio"]}

    @app.post("/api/v1/audio/tts/reference/transcribe")
    async def transcribe_tts_reference():
        current = str(settings.tts_reference_audio or "")
        if not current:
            raise HTTPException(status_code=409, detail="请先上传参考音频")
        try:
            recognized = await audio.transcribe_reference(Path(current), request_id=f"tts-reference-{uuid4().hex}")
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        text = str(recognized.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="没有识别到参考音频文字")
        result = container.config.update({"audio": {"tts_reference_text": text}})
        return {
            "success": True,
            "transcript": text,
            "duration": recognized.get("duration"),
            "settings": result["audio"],
        }

    @app.post("/api/v1/audio/asr")
    async def transcribe(
        audio_file: Annotated[UploadFile, File()],
        x_request_id: str | None = Header(default=None),
    ):
        request_id = x_request_id or uuid4().hex
        data = await audio_file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty audio file")
        try:
            text = await audio.transcribe(
                data,
                audio_file.filename or "audio.webm",
                audio_file.content_type or "audio/webm",
                request_id=request_id,
            )
        except AudioProviderUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"text": text, "request_id": request_id}

    @app.websocket("/api/v1/audio/asr/stream")
    async def stream_asr(websocket: WebSocket):
        await websocket.accept()
        provider = settings.asr_provider
        if provider == "mock":
            await websocket.send_json({"event": "asr.ready", "data": {"provider": "mock"}})
            mock_started = False
            mock_input_locked = False
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("bytes") and not mock_started and not mock_input_locked:
                        mock_started = True
                        await websocket.send_json({"event": "asr.speech_start", "data": {}})
                        await websocket.send_json({"event": "asr.partial", "data": {"text": "这是一条测试"}})
                    if message.get("text"):
                        control = json.loads(message["text"])
                        if control.get("action") == "input_gate":
                            mock_input_locked = bool(control.get("locked", False))
                            mock_started = False
                            await websocket.send_json(
                                {
                                    "event": "asr.input_gate",
                                    "data": {"locked": mock_input_locked},
                                }
                            )
                        if control.get("action") == "stop":
                            await websocket.send_json(
                                {
                                    "event": "asr.final",
                                    "data": {"text": "这是一条测试语音", "auto_send": True},
                                }
                            )
                            mock_started = False
            except (WebSocketDisconnect, RuntimeError):
                return

        if provider != "funasr":
            await websocket.send_json({"event": "asr.error", "data": {"error": f"unsupported provider: {provider}"}})
            await websocket.close(code=1011)
            return

        if settings.asr_base_url.startswith(("ws://", "wss://")):
            from websockets.asyncio.client import connect

            audio_config = container.config.snapshot(redact=False)["audio"]
            stream_state: dict[str, Any] = {"playing": False, "noise_floor_db": None}

            def apply_voice_threshold(control: dict[str, Any], playing: bool) -> None:
                stream_state["playing"] = playing
                backoff_level = max(0, min(2, int(control.get("barge_backoff_level") or 0)))
                minimum_key = "asr_barge_in_min_speech_ms" if playing else "asr_listening_min_speech_ms"
                noise_floor = stream_state.get("noise_floor_db")
                control["energy_threshold_db"] = _voice_energy_threshold_db(
                    audio_config,
                    playing=playing,
                    noise_floor_db=(float(noise_floor) if isinstance(noise_floor, (int, float)) else None),
                ) + (3.0 * backoff_level if playing else 0.0)
                control["min_speech_ms"] = int(audio_config[minimum_key]) + (120 * backoff_level if playing else 0)
                control["candidate_release_ms"] = int(audio_config["asr_candidate_release_ms"])
                control["playback_active"] = playing

            async def client_to_worker(upstream: Any) -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text"):
                        control = json.loads(message["text"])
                        if control.get("action") == "start":
                            control["silence_ms"] = int(audio_config["asr_silence_ms"])
                            control["auto_send"] = bool(audio_config["asr_auto_send"])
                            control["deferred_during_playback"] = bool(
                                audio_config.get("asr_deferred_during_playback", True)
                            )
                            control["dynamic_endpointing"] = bool(audio_config.get("asr_dynamic_endpointing", True))
                            control["final_refinement_enabled"] = bool(
                                audio_config.get("asr_final_refinement_enabled", True)
                            )
                            control["final_refinement_timeout_ms"] = int(
                                audio_config.get("asr_final_refinement_timeout_ms", 1400)
                            )
                            control["final_refinement_min_audio_ms"] = int(
                                audio_config.get("asr_final_refinement_min_audio_ms", 320)
                            )
                            control["final_refinement_max_audio_ms"] = int(
                                audio_config.get("asr_final_refinement_max_audio_ms", 15000)
                            )
                            apply_voice_threshold(control, bool(control.get("playback_active", False)))
                            if bool(audio_config.get("asr_hotwords_enabled", True)):
                                control["vocabulary"] = container.asr_vocabulary.snapshot(include_entries=False)
                        elif control.get("action") == "playback_state":
                            playing = bool(control.get("playing", False))
                            noise_floor = control.get("noise_floor_db")
                            if isinstance(noise_floor, (int, float)):
                                stream_state["noise_floor_db"] = float(noise_floor)
                            apply_voice_threshold(control, playing)
                        await upstream.send(json.dumps(control, ensure_ascii=False))

            async def worker_to_client(upstream: Any) -> None:
                async for raw in upstream:
                    event = json.loads(raw)
                    if event.get("event") in {"asr.final", "asr.deferred"}:
                        container.asr_vocabulary.record_observation(event.get("data") or {}, event=str(event["event"]))
                    await websocket.send_json(event)

            try:
                async with connect(settings.asr_base_url, max_size=8 * 1024 * 1024) as upstream:
                    tasks = {
                        asyncio.create_task(client_to_worker(upstream)),
                        asyncio.create_task(worker_to_client(upstream)),
                    }
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    # Always retrieve every task result. Otherwise a client
                    # disconnect racing the upstream close is logged as
                    # "Task exception was never retrieved" and leaves the
                    # bridge looking like a service crash.
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, asyncio.CancelledError):
                            continue
                        if isinstance(result, (WebSocketDisconnect, RuntimeError)):
                            return
                        if isinstance(result, BaseException):
                            raise result
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception as exc:  # noqa: BLE001
                try:
                    await websocket.send_json(
                        {
                            "event": "asr.error",
                            "data": {"error": f"FunASR worker unavailable: {exc}"},
                        }
                    )
                    await websocket.close(code=1011)
                except (WebSocketDisconnect, RuntimeError):
                    pass
            return

        await websocket.send_json({"event": "asr.loading", "data": {"provider": "funasr"}})
        ready = await asyncio.to_thread(audio.streaming_asr.load)
        if not ready:
            await websocket.send_json(
                {
                    "event": "asr.error",
                    "data": {"error": audio.streaming_asr.error or "FunASR load failed"},
                }
            )
            await websocket.close(code=1011)
            return
        audio_config = container.config.snapshot(redact=False)["audio"]
        options = ASRSessionOptions(
            silence_ms=int(audio_config["asr_silence_ms"]),
            energy_threshold=10 ** (float(audio_config["asr_listening_energy_threshold_db"]) / 20),
            min_speech_ms=int(audio_config["asr_listening_min_speech_ms"]),
            candidate_release_ms=int(audio_config["asr_candidate_release_ms"]),
            auto_send=bool(audio_config["asr_auto_send"]),
            deferred_during_playback=bool(audio_config.get("asr_deferred_during_playback", True)),
            dynamic_endpointing=bool(audio_config.get("asr_dynamic_endpointing", True)),
            final_refinement_enabled=bool(audio_config.get("asr_final_refinement_enabled", True)),
            final_refinement_timeout_ms=int(audio_config.get("asr_final_refinement_timeout_ms", 1400)),
            final_refinement_min_audio_ms=int(audio_config.get("asr_final_refinement_min_audio_ms", 320)),
            final_refinement_max_audio_ms=int(audio_config.get("asr_final_refinement_max_audio_ms", 15000)),
        )
        if bool(audio_config.get("asr_hotwords_enabled", True)):
            vocabulary = container.asr_vocabulary.snapshot(include_entries=False)
            options.vocabulary_revision = str(vocabulary["revision"])
            options.decoder_hotwords = tuple(vocabulary["decoder_hotwords"])
            options.explicit_corrections = dict(vocabulary["explicit"])
            options.fuzzy_targets = tuple(vocabulary["fuzzy_targets"])
        session = FunASRStreamSession(audio.streaming_asr, options)
        await websocket.send_json(
            {
                "event": "asr.ready",
                "data": {"provider": "funasr", "sample_rate": options.sample_rate},
            }
        )
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                raw_events: list[dict[str, Any]] = []
                if message.get("bytes") is not None:
                    raw_events = await asyncio.to_thread(session.feed, message["bytes"])
                elif message.get("text"):
                    control = json.loads(message["text"])
                    action = control.get("action")
                    if action == "start":
                        session.reset()
                    elif action == "playback_state":
                        playing = bool(control.get("playing", False))
                        minimum_key = "asr_barge_in_min_speech_ms" if playing else "asr_listening_min_speech_ms"
                        noise_floor = control.get("noise_floor_db")
                        backoff_level = max(0, min(2, int(control.get("barge_backoff_level") or 0)))
                        session.configure_playback(
                            playing=playing,
                            energy_threshold=10
                            ** (
                                (
                                    _voice_energy_threshold_db(
                                        audio_config,
                                        playing=playing,
                                        noise_floor_db=(
                                            float(noise_floor) if isinstance(noise_floor, (int, float)) else None
                                        ),
                                    )
                                    + (3.0 * backoff_level if playing else 0.0)
                                )
                                / 20
                            ),
                            min_speech_ms=int(audio_config[minimum_key]) + (120 * backoff_level if playing else 0),
                            candidate_release_ms=int(audio_config["asr_candidate_release_ms"]),
                            playback_text=str(control.get("playback_text") or ""),
                        )
                    elif action == "input_gate":
                        locked = bool(control.get("locked", False))
                        session.configure_input_gate(locked)
                        await websocket.send_json(
                            {
                                "event": "asr.input_gate",
                                "data": {
                                    "locked": locked,
                                    "reason": str(control.get("reason") or ""),
                                },
                            }
                        )
                    elif action == "cancel":
                        session.reset()
                        await websocket.send_json({"event": "asr.cancelled", "data": {}})
                    elif action == "stop":
                        silence = b"\x00\x00" * int(options.sample_rate * 0.5)
                        raw_events = await asyncio.to_thread(session.feed, silence, force_final=True)
                for event in raw_events:
                    if event.get("event") in {"asr.final", "asr.deferred"}:
                        pcm, playback_active = session.pop_finalized_audio()
                        if pcm:
                            refinement = await asyncio.to_thread(
                                audio.streaming_asr.refine_final_pcm,
                                pcm,
                                options,
                                playback_active=playback_active,
                            )
                            apply_final_refinement(event, refinement, session.corrector)
                        container.asr_vocabulary.record_observation(event.get("data") or {}, event=str(event["event"]))
                    await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            session.reset()


__all__ = ["register_routes"]
