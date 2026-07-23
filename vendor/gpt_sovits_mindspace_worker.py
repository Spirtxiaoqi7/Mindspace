"""Resident, switchable GPT-SoVITS worker for Mindspace.

The worker owns only the GPT-SoVITS path. CosyVoice remains a separate worker
selected by ``start-tts.ps1``. All endpoints are localhost-only and compatible
with Mindspace's existing raw PCM streaming adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import wave
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class GPTSoVITSWorker:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.RLock()
        self.started_at = time.time()
        self.code_root = Path(args.code_root).resolve()
        self.runtime_root = Path(args.runtime_root).resolve()
        self.model_root = Path(args.model_root).resolve()
        self.catalog_path = Path(args.catalog).resolve()
        self.voices = self._load_catalog(self.catalog_path)
        self.voice_id = ""
        self.voice: dict[str, Any] = {}
        self.sample_rate = 48_000

        if args.force_cpu:
            os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
        os.chdir(self.runtime_root)
        sys.path.insert(0, str(self.code_root))
        sys.path.insert(0, str(self.code_root / "GPT_SoVITS"))
        sys.path.insert(0, str(self.code_root / "GPT_SoVITS" / "eres2net"))

        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio

        # torchaudio 2.11 routes even ordinary WAV decoding through TorchCodec.
        # The Mindspace runtime already ships libsndfile via soundfile, so keep
        # upstream GPT-SoVITS untouched and fall back only when TorchCodec is
        # the missing decoder. The return value matches torchaudio.load.
        native_audio_load = torchaudio.load

        def load_audio_with_soundfile_fallback(
            uri,
            frame_offset=0,
            num_frames=-1,
            normalize=True,
            channels_first=True,
            format=None,
            buffer_size=4096,
            backend=None,
        ):
            try:
                return native_audio_load(
                    uri,
                    frame_offset=frame_offset,
                    num_frames=num_frames,
                    normalize=normalize,
                    channels_first=channels_first,
                    format=format,
                    buffer_size=buffer_size,
                    backend=backend,
                )
            except ImportError as exc:
                if "TorchCodec" not in str(exc):
                    raise
                audio, sample_rate = sf.read(uri, dtype="float32", always_2d=True)
                start = max(0, int(frame_offset))
                end = None if int(num_frames) < 0 else start + int(num_frames)
                audio = audio[start:end]
                if channels_first:
                    audio = audio.T
                tensor = torch.from_numpy(np.ascontiguousarray(audio))
                return tensor, int(sample_rate)

        torchaudio.load = load_audio_with_soundfile_fallback

        # split-lang asks fast-langdetect for its optional 126 MiB "full"
        # model on every cold start. The package already bundles the compact
        # 176-language model, which is sufficient for TTS text segmentation.
        # Force the bundled model so the Worker remains offline and portable.
        import fast_langdetect

        native_language_detect = fast_langdetect.detect

        def detect_with_bundled_model(
            text,
            *,
            model=None,
            k=1,
            threshold=0.0,
            config=None,
        ):
            selected_model = "lite" if model in (None, "auto", "full") else model
            return native_language_detect(
                text,
                model=selected_model,
                k=k,
                threshold=threshold,
                config=config,
            )

        fast_langdetect.detect = detect_with_bundled_model
        import GPT_SoVITS.TTS_infer_pack.TTS as tts_module

        # V4's vocoder path is derived from this module global. Keep third-party
        # code immutable and point it at the Launcher-managed model directory.
        tts_module.now_dir = str(self.runtime_root)
        self.np = np
        self.torch = torch
        self.sf = sf
        self.TTS = tts_module.TTS
        self.TTS_Config = tts_module.TTS_Config
        self.engine = None
        self._load_voice(args.voice, initial=True)
        if args.warmup_text:
            self._warmup(args.warmup_text)

    @staticmethod
    def _load_catalog(path: Path) -> dict[str, dict[str, Any]]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        voices = raw.get("voices") if isinstance(raw, dict) else None
        if not isinstance(voices, list) or not voices:
            raise RuntimeError("GPT-SoVITS voice catalog is empty")
        result = {}
        for item in voices:
            if isinstance(item, dict) and item.get("id"):
                result[str(item["id"])] = item
        return result

    def _voice_paths(self, voice: dict[str, Any]) -> dict[str, Path]:
        model_root = self.model_root.resolve()
        root = (model_root / str(voice["directory"])).resolve()
        paths = {
            "root": root,
            "gpt": (root / str(voice["gpt_weight"])).resolve(),
            "sovits": (root / str(voice["sovits_weight"])).resolve(),
            "reference": (root / str(voice["reference_audio"])).resolve(),
        }
        for name, path in paths.items():
            if path != model_root and model_root not in path.parents:
                raise ValueError(f"GPT-SoVITS voice path escapes model root: {name}")
            if name != "root" and not path.is_file():
                raise FileNotFoundError(f"GPT-SoVITS voice file is missing: {path}")
        return paths

    def _base_paths(self) -> dict[str, Path]:
        base = self.runtime_root / "GPT_SoVITS" / "pretrained_models"
        paths = {
            "bert": base / "chinese-roberta-wwm-ext-large",
            "hubert": base / "chinese-hubert-base",
            "vocoder": base / "gsv-v4-pretrained" / "vocoder.pth",
            "g2pw": self.runtime_root / "GPT_SoVITS" / "text" / "G2PWModel" / "g2pW.onnx",
        }
        for path in paths.values():
            if not path.exists():
                raise FileNotFoundError(f"GPT-SoVITS base model is missing: {path}")
        return paths

    def _config_for(self, voice: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
        base = self._base_paths()
        return {
            "custom": {
                "device": "cpu" if self.args.force_cpu else self.args.device,
                "is_half": bool(self.args.fp16 and not self.args.force_cpu),
                "version": str(voice["family"]),
                "t2s_weights_path": str(paths["gpt"]),
                "vits_weights_path": str(paths["sovits"]),
                "bert_base_path": str(base["bert"]),
                "cnhuhbert_base_path": str(base["hubert"]),
            }
        }

    def _load_voice(self, voice_id: str, *, initial: bool = False) -> None:
        if voice_id not in self.voices:
            raise ValueError(f"unknown GPT-SoVITS voice: {voice_id}")
        if not initial and voice_id == self.voice_id:
            return
        voice = self.voices[voice_id]
        paths = self._voice_paths(voice)
        with self.lock:
            if initial or self.engine is None:
                config = self.TTS_Config(self._config_for(voice, paths))
                self.engine = self.TTS(config)
            else:
                self.engine.init_t2s_weights(str(paths["gpt"]))
                self.engine.init_vits_weights(str(paths["sovits"]))
            self.engine.set_ref_audio(str(paths["reference"]))
            self.voice_id = voice_id
            self.voice = voice
            self.sample_rate = int(voice["sample_rate"])

    def select_voice(self, voice_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        # Voice packs can be installed while this long-lived worker is running.
        # Reload the small catalog atomically before every explicit switch so a
        # newly downloaded voice does not require a full application restart.
        refreshed = self._load_catalog(self.catalog_path)
        self.voices = refreshed
        self._load_voice(voice_id)
        self._warmup("声音已经切换完成。")
        return {
            "ok": True,
            "voice_id": self.voice_id,
            "voice_label": self.voice["label"],
            "family": self.voice["family"],
            "sample_rate": self.sample_rate,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    def health(self) -> dict[str, Any]:
        cuda = bool(self.torch.cuda.is_available())
        return {
            "ok": self.engine is not None,
            "loaded": self.engine is not None,
            "provider": "gpt-sovits",
            "voice_id": self.voice_id,
            "voice_label": self.voice.get("label", ""),
            "family": self.voice.get("family", ""),
            "sample_rate": self.sample_rate,
            "cuda_available": cuda,
            "device": self.torch.cuda.get_device_name(0) if cuda else "cpu",
            "fp16": bool(self.args.fp16 and not self.args.force_cpu),
            "catalog_voices": len(self.voices),
            "uptime_sec": round(time.time() - self.started_at, 2),
        }

    def _inputs(self, text: str, speed: float) -> dict[str, Any]:
        paths = self._voice_paths(self.voice)
        family = str(self.voice["family"])
        configured = self.voice.get("prosody")
        prosody = configured if isinstance(configured, dict) else {}
        split_method = str(prosody.get("text_split_method") or "cut0")
        if split_method not in {"cut0", "cut1", "cut2", "cut3", "cut4", "cut5"}:
            split_method = "cut0"
        return {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(paths["reference"]),
            "prompt_text": str(self.voice["reference_text"]),
            "prompt_lang": str(self.voice.get("reference_language") or "zh"),
            "top_k": max(1, min(100, int(prosody.get("top_k", 5)))),
            "top_p": max(0.05, min(1.0, float(prosody.get("top_p", 1.0)))),
            "temperature": max(0.05, min(2.0, float(prosody.get("temperature", 1.0)))),
            "text_split_method": split_method,
            "batch_size": 1,
            "batch_threshold": 0.75,
            "split_bucket": False,
            "speed_factor": max(0.5, min(2.0, speed)),
            "fragment_interval": max(
                0.0, min(1.0, float(prosody.get("fragment_interval", 0.01)))
            ),
            "seed": -1,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
            "sample_steps": 8,
            "super_sampling": False,
            "streaming_mode": family != "v4",
            # V3/V4 cannot stream semantic tokens through the vocoder, but the
            # official pipeline does support returning punctuation fragments.
            # This avoids long low-level gaps produced by batched V4 decoding
            # and lets Mindspace play the first complete clause immediately.
            "return_fragment": True,
        }

    def _pcm(self, audio: Any) -> bytes:
        array = self.np.asarray(audio)
        if array.dtype != self.np.int16:
            array = self.np.clip(array, -1.0, 1.0)
            array = (array * 32767).round().astype(self.np.int16)
        return array.reshape(-1).tobytes()

    def _trim_boundary_silence(self, pcm: bytes) -> bytes:
        """Remove pathological V4 boundary gaps while preserving cadence."""

        samples = self.np.frombuffer(pcm, dtype=self.np.int16)
        if samples.size == 0:
            return pcm
        active = self.np.flatnonzero(self.np.abs(samples.astype(self.np.int32)) >= 64)
        if active.size == 0:
            return pcm
        keep_head = int(self.sample_rate * 0.08)
        keep_tail = int(self.sample_rate * 0.18)
        start = max(0, int(active[0]) - keep_head)
        end = min(samples.size, int(active[-1]) + 1 + keep_tail)
        return samples[start:end].tobytes()

    def stream_synthesize(self, payload: dict[str, Any]) -> Iterator[bytes]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("empty TTS text")
        requested = str(payload.get("voice_id") or self.voice_id)
        speed = float(payload.get("speed") or 1.0)
        with self.lock:
            if requested != self.voice_id:
                self._load_voice(requested)
            for sample_rate, audio in self.engine.run(self._inputs(text, speed)):
                self.sample_rate = int(sample_rate)
                pcm = self._trim_boundary_silence(self._pcm(audio))
                for offset in range(0, len(pcm), 32_768):
                    yield pcm[offset : offset + 32_768]

    def synthesize(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = Path(str(payload.get("output") or "")).resolve()
        if not str(payload.get("output") or "").strip():
            raise ValueError("missing output path")
        started = time.perf_counter()
        pcm = b"".join(self.stream_synthesize(payload))
        if not pcm:
            raise RuntimeError("GPT-SoVITS did not return audio")
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(self.sample_rate)
            stream.writeframes(pcm)
        return {
            "ok": True,
            "output": str(output),
            "bytes": output.stat().st_size,
            "sample_rate": self.sample_rate,
            "voice_id": self.voice_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    def _warmup(self, text: str) -> None:
        for _ in self.stream_synthesize({"text": text, "speed": 1.0, "voice_id": self.voice_id}):
            pass


def make_handler(worker: GPTSoVITSWorker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MindspaceGPTSoVITS/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("request body must be an object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/health":
                self._json(200, worker.health())
                return
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._payload()
                route = self.path.rstrip("/")
                if route == "/voice":
                    self._json(200, worker.select_voice(str(payload.get("voice_id") or "")))
                    return
                if route == "/synthesize":
                    self._json(200, worker.synthesize(payload))
                    return
                if route == "/synthesize-stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    for chunk in worker.stream_synthesize(payload):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                self._json(404, {"ok": False, "error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # noqa: BLE001
                try:
                    self._json(500, {"ok": False, "error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    pass

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--voice", default="v4-changli")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--force-cpu", action="store_true")
    parser.add_argument("--warmup-text", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    worker = GPTSoVITSWorker(args)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(worker))
    print(json.dumps(worker.health(), ensure_ascii=False), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
