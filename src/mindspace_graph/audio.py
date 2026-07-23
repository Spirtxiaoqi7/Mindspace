"""Service-oriented TTS/ASR adapters with browser fallbacks and cancellation."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx

from mindspace_graph.gpt_sovits import voice_definition, voice_is_installed
from mindspace_graph.settings import AppSettings
from mindspace_graph.streaming_asr import FunASRRuntime


class AudioProviderUnavailable(RuntimeError):
    pass


def sanitize_tts_text(text: str) -> str:
    """Remove non-spoken parenthetical directions without altering displayed text."""

    output: list[str] = []
    closers: list[str] = []
    for character in text or "":
        if character in {"（", "("}:
            closers.append("）" if character == "（" else ")")
            continue
        if closers:
            if character == closers[-1]:
                closers.pop()
            elif character in {"（", "("}:
                closers.append("）" if character == "（" else ")")
            continue
        if character in {"）", ")"}:
            continue
        output.append(character)
    return " ".join("".join(output).split()).strip()


class AudioService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._tasks: dict[str, asyncio.Task] = {}
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self.streaming_asr = FunASRRuntime(settings.model_root / "asr", device=settings.asr_device)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def status(self) -> dict[str, object]:
        result: dict[str, object] = {
            "tts_provider": self.settings.tts_provider,
            "asr_provider": self.settings.asr_provider,
            "browser_tts": True,
            "browser_asr": True,
            "tts_ready": self.settings.tts_provider in {"browser", "mock"},
            "asr_ready": self.settings.asr_provider in {"browser", "mock"},
        }
        if self.settings.tts_provider in {"cosyvoice", "gpt-sovits"}:
            worker_url = (
                self.settings.tts_worker_url
                if self.settings.tts_provider == "cosyvoice"
                else self.settings.tts_gpt_sovits_worker_url
            )
            try:
                response = await self._http.get(
                    f"{worker_url.rstrip('/')}/health", timeout=2
                )
                response.raise_for_status()
                health = response.json()
                result["tts_ready"] = bool(health.get("ok"))
                result["tts_detail"] = {
                    key: health.get(key)
                    for key in (
                        "loaded",
                        "device",
                        "sample_rate",
                        "provider",
                        "voice_id",
                        "voice_label",
                    )
                }
                if self.settings.tts_provider == "gpt-sovits":
                    voice_id = self.settings.tts_gpt_sovits_voice
                    if not voice_is_installed(self.settings.model_root, voice_id):
                        result["tts_ready"] = False
                        result["tts_error"] = f"音色尚未安装：{voice_definition(voice_id)['label']}"
                    elif health.get("voice_id") != voice_id:
                        result["tts_ready"] = False
                        result["tts_error"] = "GPT-SoVITS Worker 当前音色与设置不一致"
            except Exception as exc:  # noqa: BLE001
                result["tts_ready"] = False
                result["tts_error"] = str(exc)
        elif self.settings.tts_provider == "siliconflow":
            configured = bool(
                self.settings.tts_siliconflow_base_url
                and self.settings.tts_siliconflow_api_key
                and self.settings.tts_siliconflow_model
                and self.settings.tts_siliconflow_voice
            )
            result["tts_ready"] = configured
            result["tts_detail"] = {
                "provider": "siliconflow",
                "model": self.settings.tts_siliconflow_model,
                "voice": self.settings.tts_siliconflow_voice,
                "sample_rate": self.settings.tts_siliconflow_sample_rate,
                "credentials_configured": bool(self.settings.tts_siliconflow_api_key),
            }
            if not configured:
                result["tts_error"] = "SiliconFlow TTS 配置不完整"
        if self.settings.asr_provider == "openai":
            result["asr_ready"] = bool(self.settings.asr_base_url)
        elif self.settings.asr_provider == "funasr":
            worker_url = self.settings.asr_base_url
            worker_health = worker_url.replace("ws://", "http://").replace("wss://", "https://")
            if worker_health.endswith("/ws"):
                worker_health = f"{worker_health[:-3]}/health"
            try:
                response = await self._http.get(worker_health, timeout=1)
                response.raise_for_status()
                detail = response.json()
                result["asr_ready"] = bool(detail.get("ready"))
                result["asr_detail"] = {**detail, "worker_url": worker_url}
            except Exception as exc:  # noqa: BLE001
                detail = await asyncio.to_thread(self.streaming_asr.status)
                result["asr_ready"] = bool(detail["ready"])
                result["asr_detail"] = {
                    **detail,
                    "worker_url": worker_url,
                    "worker_error": str(exc),
                }
        return result

    def _register_current(self, request_id: str) -> None:
        task = asyncio.current_task()
        if request_id and task:
            self._tasks[request_id] = task

    def _finish(self, request_id: str) -> None:
        if request_id:
            self._tasks.pop(request_id, None)

    def interrupt(self, request_id: str) -> bool:
        task = self._tasks.get(request_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def synthesize(self, text: str, *, request_id: str, speed: float = 1.0) -> Path:
        self._register_current(request_id)
        try:
            text = sanitize_tts_text(text)
            if not text:
                raise AudioProviderUnavailable("没有可朗读的正文内容")
            provider = self.settings.tts_provider
            if provider == "browser":
                raise AudioProviderUnavailable("browser_tts")
            output = self.settings.runtime_dir / "data" / "audio" / f"{uuid4().hex}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            if provider == "mock":
                await asyncio.to_thread(self._write_silent_wav, output)
                return output
            if provider == "siliconflow":
                pcm = bytearray()
                async for chunk in self._siliconflow_chunks(text, speed=speed):
                    pcm.extend(chunk)
                if not pcm:
                    raise AudioProviderUnavailable("SiliconFlow 没有返回音频")
                await asyncio.to_thread(
                    self._write_pcm16_wav,
                    output,
                    bytes(pcm),
                    self.settings.tts_siliconflow_sample_rate,
                )
                return output
            if provider not in {"cosyvoice", "gpt-sovits"}:
                raise AudioProviderUnavailable(f"unsupported TTS provider: {provider}")
            worker_url = (
                self.settings.tts_worker_url
                if provider == "cosyvoice"
                else self.settings.tts_gpt_sovits_worker_url
            )
            payload = {
                "text": text,
                "output": str(output),
                "speed": max(0.5, min(2.0, speed)),
            }
            if provider == "cosyvoice":
                payload.update(
                    reference=self.settings.tts_reference_audio,
                    reference_text=self.settings.tts_reference_text,
                )
            else:
                payload["voice_id"] = self.settings.tts_gpt_sovits_voice
            response = await self._http.post(
                f"{worker_url.rstrip('/')}/synthesize", json=payload, timeout=180
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok") or not output.exists() or output.stat().st_size == 0:
                raise RuntimeError(result.get("error") or f"{provider} did not create audio")
            return output
        finally:
            self._finish(request_id)

    async def stream_synthesize(
        self, text: str, *, request_id: str, speed: float = 1.0
    ) -> tuple[AsyncIterator[bytes], int]:
        """Return raw mono PCM16 as it is produced instead of buffering a WAV."""

        text = sanitize_tts_text(text)
        if not text:
            raise AudioProviderUnavailable("没有可朗读的正文内容")
        provider = self.settings.tts_provider
        if provider == "browser":
            raise AudioProviderUnavailable("browser_tts")
        if provider not in {"mock", "cosyvoice", "siliconflow", "gpt-sovits"}:
            raise AudioProviderUnavailable(f"unsupported TTS provider: {provider}")

        sample_rate = (
            16_000
            if provider == "mock"
            else self.settings.tts_siliconflow_sample_rate
            if provider == "siliconflow"
            else int(voice_definition(self.settings.tts_gpt_sovits_voice)["sample_rate"])
            if provider == "gpt-sovits"
            else 24_000
        )

        async def generate() -> AsyncIterator[bytes]:
            self._register_current(request_id)
            try:
                if provider == "mock":
                    yield b"\x00\x00" * 3_200
                    return
                if provider == "siliconflow":
                    async for chunk in self._siliconflow_chunks(text, speed=speed):
                        yield chunk
                    return
                worker_url = (
                    self.settings.tts_worker_url
                    if provider == "cosyvoice"
                    else self.settings.tts_gpt_sovits_worker_url
                )
                payload = {
                    "text": text,
                    "speed": max(0.5, min(2.0, speed)),
                }
                if provider == "cosyvoice":
                    payload.update(
                        reference=self.settings.tts_reference_audio,
                        reference_text=self.settings.tts_reference_text,
                    )
                else:
                    payload["voice_id"] = self.settings.tts_gpt_sovits_voice
                timeout = httpx.Timeout(connect=5, read=180, write=10, pool=5)
                async with self._http.stream(
                    "POST",
                    f"{worker_url.rstrip('/')}/synthesize-stream",
                    json=payload,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        if chunk:
                            yield chunk
            finally:
                self._finish(request_id)

        return generate(), sample_rate

    async def select_gpt_sovits_voice(self, voice_id: str) -> dict[str, object]:
        voice = voice_definition(voice_id)
        if not voice_is_installed(self.settings.model_root, voice_id):
            raise AudioProviderUnavailable(f"请先在启动器下载音色：{voice['label']}")
        endpoint = f"{self.settings.tts_gpt_sovits_worker_url.rstrip('/')}/voice"
        try:
            response = await self._http.post(
                endpoint, json={"voice_id": voice_id}, timeout=180
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPError as exc:
            raise AudioProviderUnavailable(
                f"GPT-SoVITS 音色已保存，Worker 切换失败：{exc}"
            ) from exc
        if not result.get("ok"):
            raise AudioProviderUnavailable(str(result.get("error") or "GPT-SoVITS 音色切换失败"))
        return result

    def _siliconflow_payload(self, text: str, speed: float) -> dict[str, object]:
        if not self.settings.tts_siliconflow_api_key:
            raise AudioProviderUnavailable("请先配置 SiliconFlow API 密钥")
        if not self.settings.tts_siliconflow_model:
            raise AudioProviderUnavailable("请配置 SiliconFlow TTS 模型")
        if not self.settings.tts_siliconflow_voice:
            raise AudioProviderUnavailable("请配置 SiliconFlow 音色")
        return {
            "model": self.settings.tts_siliconflow_model,
            "input": text,
            "voice": self.settings.tts_siliconflow_voice,
            "response_format": "pcm",
            "sample_rate": self.settings.tts_siliconflow_sample_rate,
            "stream": True,
            "speed": max(0.5, min(2.0, float(speed))),
            "gain": self.settings.tts_siliconflow_gain,
        }

    async def _siliconflow_chunks(self, text: str, *, speed: float) -> AsyncIterator[bytes]:
        payload = self._siliconflow_payload(text, speed)
        endpoint = f"{self.settings.tts_siliconflow_base_url.rstrip('/')}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.settings.tts_siliconflow_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/audio",
        }
        timeout = httpx.Timeout(connect=10, read=180, write=10, pool=10)
        try:
            async with self._http.stream(
                "POST", endpoint, headers=headers, json=payload, timeout=timeout
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(detail)
                        detail = str(
                            parsed.get("message")
                            or parsed.get("error")
                            or parsed.get("detail")
                            or detail
                        )
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    raise AudioProviderUnavailable(
                        f"SiliconFlow TTS 请求失败（{response.status_code}）：{detail[:300]}"
                    )
                async for chunk in response.aiter_raw():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise AudioProviderUnavailable(f"无法连接 SiliconFlow TTS：{exc}") from exc

    async def transcribe(
        self, audio: bytes, filename: str, content_type: str, *, request_id: str
    ) -> str:
        self._register_current(request_id)
        try:
            provider = self.settings.asr_provider
            if provider == "browser":
                raise AudioProviderUnavailable("browser_asr")
            if provider == "mock":
                return "这是一条测试语音"
            if provider == "funasr":
                raise AudioProviderUnavailable("use /api/v1/audio/asr/stream for FunASR PCM")
            if provider != "openai" or not self.settings.asr_base_url:
                raise AudioProviderUnavailable(f"unsupported ASR provider: {provider}")
            headers = (
                {"Authorization": f"Bearer {self.settings.asr_api_key}"}
                if self.settings.asr_api_key
                else {}
            )
            files = {"file": (filename or "audio.webm", audio, content_type or "audio/webm")}
            data = {"model": self.settings.asr_model}
            response = await self._http.post(
                f"{self.settings.asr_base_url.rstrip('/')}/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=180,
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("text") or "").strip()
        finally:
            self._finish(request_id)

    async def transcribe_reference(self, path: Path, *, request_id: str) -> dict[str, object]:
        if not path.is_file():
            raise AudioProviderUnavailable("参考音频文件不存在，请重新上传")
        content = await asyncio.to_thread(path.read_bytes)
        provider = self.settings.asr_provider
        if provider != "funasr":
            text = await self.transcribe(
                content,
                path.name,
                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                request_id=request_id,
            )
            return {"text": text, "duration": None}

        worker_url = self.settings.asr_base_url.replace("ws://", "http://").replace(
            "wss://", "https://"
        )
        endpoint = (
            f"{worker_url[:-3]}/transcribe"
            if worker_url.endswith("/ws")
            else f"{worker_url.rstrip('/')}/transcribe"
        )
        try:
            response = await self._http.post(
                endpoint,
                content=content,
                headers={
                    "Content-Type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                    "X-Audio-Filename": path.name,
                },
                timeout=180,
            )
            if response.is_error:
                try:
                    detail = str(response.json().get("detail") or response.text)
                except ValueError:
                    detail = response.text
                raise AudioProviderUnavailable(f"参考音频识别失败：{detail}")
            payload = response.json()
        except httpx.HTTPError as exc:
            raise AudioProviderUnavailable(f"无法连接实时识别服务：{exc}") from exc
        text = str(payload.get("text") or "").strip()
        if not text:
            raise AudioProviderUnavailable("没有识别到清晰语音，请换用更干净的参考音频")
        return {
            "text": text,
            "duration": payload.get("duration"),
            "sample_rate": payload.get("sample_rate"),
        }

    @staticmethod
    def _write_silent_wav(path: Path) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 3200)

    @staticmethod
    def _write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
