"""Resident CosyVoice worker for Mindspace TTS.

The Flask app keeps this process alive and sends synthesis jobs over localhost.
CosyVoice is loaded once here, so chat auto-play does not reload the model for
every assistant message.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _patch_torchaudio_io(torchaudio_module) -> None:
    """Use soundfile for WAV I/O to avoid TorchCodec/FFmpeg runtime coupling."""
    import soundfile as sf
    import torch

    def _load_with_soundfile(path, *_, **__):
        data, sample_rate = sf.read(str(path), always_2d=True, dtype="float32")
        # Voice cloning expects a single clean prompt channel. Browser-side
        # conversion is best-effort, so normalize stereo files here as well.
        if data.shape[1] > 1:
            data = data.mean(axis=1, keepdims=True)
        return torch.from_numpy(data.T).contiguous(), sample_rate

    torchaudio_module.load = _load_with_soundfile


class CosyVoiceWorker:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.repo_dir = Path(__file__).resolve().parent / "CosyVoice"
        sys.path.insert(0, str(self.repo_dir))
        sys.path.insert(0, str(self.repo_dir / "third_party" / "Matcha-TTS"))
        if args.force_cpu:
            os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

        import torch
        import torchaudio
        import soundfile as sf
        from cosyvoice.cli.cosyvoice import AutoModel

        _patch_torchaudio_io(torchaudio)
        self.torch = torch
        self.sf = sf
        self.model_dir = str(Path(args.model_dir))
        self.reference = str(Path(args.reference))
        self.reference_text = self._normalize_prompt_text(args.reference_text)
        self.model = AutoModel(
            model_dir=self.model_dir,
            fp16=bool(args.fp16),
            load_trt=False,
            load_vllm=False,
        )
        self.sample_rate = self.model.sample_rate
        self.stream_token_hop_len = int(getattr(self.model.model, "token_hop_len", 25))
        self.speaker_cache = Path(args.speaker_cache).resolve() if args.speaker_cache else None
        self.persistent_speaker_hit = False
        self._load_speaker_cache()
        self.cached_reference_key = ""
        self.cached_speaker_id = ""
        self._speaker_profile(self.reference, self.reference_text)
        if args.warmup_text:
            self._warmup(args.warmup_text)

    @staticmethod
    def _normalize_prompt_text(text: str) -> str:
        prompt_text = (text or "").strip()
        if "<|endofprompt|>" not in prompt_text:
            prompt_text = f"You are a helpful assistant.<|endofprompt|>{prompt_text or '这是一段用于声音克隆的参考音频。'}"
        return prompt_text

    def health(self) -> dict[str, Any]:
        cuda_available = bool(self.torch.cuda.is_available())
        return {
            "ok": True,
            "loaded": True,
            "model_dir": self.model_dir,
            "sample_rate": self.sample_rate,
            "uptime_sec": round(time.time() - self.started_at, 2),
            "cuda_available": cuda_available,
            "device": self.torch.cuda.get_device_name(0) if cuda_available else "cpu",
            "reference_cached": bool(self.cached_speaker_id),
            "speaker_id": self.cached_speaker_id,
            "speaker_cache": str(self.speaker_cache) if self.speaker_cache else "",
            "speaker_persisted": bool(
                self.speaker_cache
                and self.speaker_cache.is_file()
                and self.cached_speaker_id in self.model.frontend.spk2info
            ),
            "persistent_speaker_hit": self.persistent_speaker_hit,
            "fp16": bool(self.args.fp16),
            "stream_token_hop_len": self.stream_token_hop_len,
        }

    def _warmup(self, text: str) -> None:
        with self.lock:
            for _ in self.model.inference_zero_shot(
                text,
                self.reference_text,
                self.reference,
                zero_shot_spk_id=self.cached_speaker_id,
                stream=False,
                speed=1.0,
                text_frontend=True,
            ):
                pass

    def _pcm_bytes(self, speech, speed: float = 1.0) -> bytes:
        speech = speech.float().cpu()
        if speed != 1.0 and speech.numel() > 1:
            target = max(1, int(speech.numel() / speed))
            speech = self.torch.nn.functional.interpolate(
                speech[None, None, :], size=target, mode="linear", align_corners=False
            )[0, 0]
        speech = speech - speech.mean()
        peak = float(speech.abs().max()) if speech.numel() else 0.0
        rms = float(speech.square().mean().sqrt()) if speech.numel() else 0.0
        if peak and rms:
            gain = min(3.0, 0.075 / rms, 0.95 / peak)
            speech = speech * gain
        return (
            speech.clamp(-1, 1)
            .mul(32767)
            .round()
            .to(self.torch.int16)
            .numpy()
            .tobytes()
        )

    def _load_speaker_cache(self) -> None:
        if not self.speaker_cache or not self.speaker_cache.is_file():
            return
        cached = self.torch.load(
            str(self.speaker_cache),
            map_location=self.model.frontend.device,
            weights_only=True,
        )
        if isinstance(cached, dict):
            self.model.frontend.spk2info.update(cached)

    def _persist_speaker_cache(self, active_speaker_id: str) -> None:
        if not self.speaker_cache:
            return
        self.speaker_cache.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            active_speaker_id: self.model.frontend.spk2info[active_speaker_id],
        }
        temporary = self.speaker_cache.with_suffix(f"{self.speaker_cache.suffix}.tmp")
        self.torch.save(payload, str(temporary))
        os.replace(temporary, self.speaker_cache)

    def _speaker_profile(self, reference: str, reference_text: str) -> str:
        path = Path(reference).resolve()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        identity = f"speaker-cache-v1|{Path(self.model_dir).name}|{digest.hexdigest()}|{reference_text}"
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if key == self.cached_reference_key:
            return self.cached_speaker_id
        speaker_id = f"mindspace-{key[:16]}"
        self.persistent_speaker_hit = speaker_id in self.model.frontend.spk2info
        if not self.persistent_speaker_hit:
            self.model.add_zero_shot_spk(reference_text, str(path), speaker_id)
        for known_id in list(self.model.frontend.spk2info):
            if known_id.startswith("mindspace-") and known_id != speaker_id:
                self.model.frontend.spk2info.pop(known_id, None)
        self._persist_speaker_cache(speaker_id)
        self.cached_reference_key = key
        self.cached_speaker_id = speaker_id
        return speaker_id

    def synthesize(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        output = Path(str(payload.get("output") or "")).resolve()
        if not text:
            raise ValueError("empty TTS text")
        if not output:
            raise ValueError("missing output path")
        output.parent.mkdir(parents=True, exist_ok=True)
        reference = str(Path(str(payload.get("reference") or self.reference)))
        reference_text = self._normalize_prompt_text(str(payload.get("reference_text") or self.reference_text))
        speed = float(payload.get("speed") or 1.0)

        started = time.time()
        with self.lock:
            speaker_id = self._speaker_profile(reference, reference_text)
            chunks = []
            for item in self.model.inference_zero_shot(
                text,
                reference_text,
                reference,
                zero_shot_spk_id=speaker_id,
                stream=False,
                speed=speed,
                text_frontend=True,
            ):
                chunks.append(item["tts_speech"].cpu())
        if not chunks:
            raise RuntimeError("CosyVoice did not return audio")
        speech = self.torch.cat(chunks, dim=1).squeeze(0).float()
        # Sentence-level synthesis can otherwise expose tiny DC jumps and clicks
        # at clip boundaries. Preserve loudness, attenuate only actual clipping,
        # and apply a transparent 6 ms edge fade before PCM16 encoding.
        speech = speech - speech.mean()
        peak = float(speech.abs().max()) if speech.numel() else 0.0
        rms = float(speech.square().mean().sqrt()) if speech.numel() else 0.0
        if peak and rms:
            # CosyVoice output is often around -30 dBFS. Lift it conservatively
            # towards -22.5 dBFS without clipping or excessive noise gain.
            gain = min(3.0, 0.075 / rms, 0.95 / peak)
            speech = speech * gain
        fade_samples = min(int(self.sample_rate * 0.006), speech.numel() // 2)
        if fade_samples > 1:
            ramp = self.torch.linspace(0, 1, fade_samples, dtype=speech.dtype)
            speech[:fade_samples] *= ramp
            speech[-fade_samples:] *= ramp.flip(0)
        self.sf.write(
            str(output),
            speech.numpy(),
            self.sample_rate,
            subtype="PCM_16",
        )
        return {
            "ok": True,
            "output": str(output),
            "bytes": output.stat().st_size if output.exists() else 0,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    def stream_synthesize(self, payload: dict[str, Any]):
        """Yield raw mono PCM16 chunks from CosyVoice's native streaming path."""

        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("empty TTS text")
        reference = str(Path(str(payload.get("reference") or self.reference)))
        reference_text = self._normalize_prompt_text(
            str(payload.get("reference_text") or self.reference_text)
        )
        speed = float(payload.get("speed") or 1.0)

        with self.lock:
            speaker_id = self._speaker_profile(reference, reference_text)
            # CosyVoice grows this value within one request. Reset it for every
            # utterance; otherwise short replies silently fall back to full-buffer
            # generation after the first streaming call.
            self.model.model.token_hop_len = self.stream_token_hop_len
            try:
                for item in self.model.inference_zero_shot(
                    text,
                    reference_text,
                    reference,
                    zero_shot_spk_id=speaker_id,
                    stream=True,
                    speed=1.0,
                    text_frontend=True,
                ):
                    pcm = self._pcm_bytes(item["tts_speech"].squeeze(0), speed)
                    if pcm:
                        yield pcm
            finally:
                self.model.model.token_hop_len = self.stream_token_hop_len


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(worker: CosyVoiceWorker):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/health":
                _json_response(self, 200, worker.health())
                return
            if self.path.rstrip("/") in {"", "/"}:
                data = worker.health()
                html = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>Mindspace TTS Worker</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f6efe7;color:#2f241d;margin:40px;line-height:1.7}}
.card{{max-width:760px;background:#fffaf4;border:1px solid #ead9c7;border-radius:20px;padding:28px;box-shadow:0 16px 40px rgba(74,44,20,.12)}}
h1{{margin-top:0}} code{{background:#f1e3d3;padding:2px 6px;border-radius:6px}}
</style>
<div class="card">
<h1>Mindspace TTS Worker 已常驻</h1>
<p>模型：<code>{data.get("model_dir")}</code></p>
<p>设备：<code>{data.get("device")}</code>，CUDA：<code>{data.get("cuda_available")}</code></p>
<p>采样率：<code>{data.get("sample_rate")}</code>，运行：<code>{data.get("uptime_sec")} 秒</code></p>
<p>机器接口：<a href="/health">/health</a></p>
</div>
"""
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _json_response(self, 404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            endpoint = self.path.rstrip("/")
            if endpoint not in {"/synthesize", "/synthesize-stream"}:
                _json_response(self, 404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(raw or "{}")
                if endpoint == "/synthesize-stream":
                    chunks = iter(worker.stream_synthesize(payload))
                    first = next(chunks)
                    self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-Audio-Format", "pcm_s16le")
                    self.send_header("X-Audio-Sample-Rate", str(worker.sample_rate))
                    self.send_header("X-Audio-Channels", "1")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(first)
                    self.wfile.flush()
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    self.close_connection = True
                    return
                _json_response(self, 200, worker.synthesize(payload))
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001
                _json_response(self, 500, {"ok": False, "error": str(exc)})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--reference-text", default="")
    parser.add_argument("--speaker-cache", default="")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--warmup-text", default="语音服务预热。")
    parser.add_argument("--force-cpu", action="store_true")
    args = parser.parse_args()

    worker = CosyVoiceWorker(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(worker))
    print(json.dumps({"ok": True, "event": "worker_started", "host": args.host, "port": args.port}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
