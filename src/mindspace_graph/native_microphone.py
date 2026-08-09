"""Resident native microphone capture for the local ASR worker.

The ASR worker, rather than Chromium, owns the Windows input endpoint.  PCM is
only fanned out to live WebSocket subscribers and is never persisted.  Keeping
the endpoint open makes a quick close/reopen of the call panel a subscription
change instead of another Windows device-open race.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InputEndpoint:
    """A PortAudio input endpoint and its native sample rate."""

    device_index: int
    device_name: str
    hostapi: str
    sample_rate: int


def _usable_input(devices: list[dict[str, Any]], index: object) -> int | None:
    try:
        candidate = int(index)
    except (TypeError, ValueError):
        return None
    if candidate < 0 or candidate >= len(devices):
        return None
    if int(devices[candidate].get("max_input_channels") or 0) < 1:
        return None
    return candidate


def select_input_endpoints(sounddevice: Any, remembered: dict[str, Any] | None = None) -> list[InputEndpoint]:
    """Return deterministic Windows candidates, preferring native WASAPI.

    The previous implementation forced MME at 16 kHz.  On the actual HyperX
    endpoint that was the only path which routinely spent seconds before the
    first callback.  WASAPI is opened at its device rate and resampled locally
    below, so device selection no longer trades startup reliability for ASR's
    16 kHz input requirement.
    """

    devices = list(sounddevice.query_devices())
    hostapis = list(sounddevice.query_hostapis())
    api_names = {index: str(api.get("name") or "unknown") for index, api in enumerate(hostapis)}
    priority = {
        "windows wasapi": 0,
        "windows wdm-ks": 1,
        "windows directsound": 2,
        "mme": 3,
    }
    candidates: list[InputEndpoint] = []
    for index, device in enumerate(devices):
        if _usable_input(devices, index) is None:
            continue
        hostapi = api_names.get(int(device.get("hostapi") or -1), "unknown")
        native_rate = int(round(float(device.get("default_samplerate") or 16_000)))
        # MME remains the final compatibility fallback.  Other APIs should be
        # opened at the format the Windows endpoint advertises.
        rate = 16_000 if hostapi.casefold() == "mme" else max(8_000, native_rate)
        candidates.append(
            InputEndpoint(
                device_index=index,
                device_name=str(device.get("name") or f"device-{index}"),
                hostapi=hostapi,
                sample_rate=rate,
            )
        )
    if not candidates:
        raise RuntimeError("Windows 未检测到可用的麦克风输入端点；请重新插拔或启用 HyperX Cloud III 麦克风")

    def sort_key(endpoint: InputEndpoint) -> tuple[int, int, int]:
        is_remembered = bool(remembered) and (
            endpoint.device_name == str(remembered.get("device_name") or "")
            and endpoint.hostapi == str(remembered.get("hostapi") or "")
            and endpoint.sample_rate == int(remembered.get("sample_rate") or 0)
        )
        api_rank = priority.get(endpoint.hostapi.casefold(), 10)
        return (0 if is_remembered else 1, api_rank, endpoint.device_index)

    return sorted(candidates, key=sort_key)


def select_input_device(sounddevice: Any) -> int:
    """Compatibility helper used by older callers and focused tests."""

    return select_input_endpoints(sounddevice)[0].device_index


class NativeMicrophoneCapture:
    """One process-lifetime PCM16 stream with bounded in-memory subscribers."""

    def __init__(self, *, sample_rate: int = 16_000, block_ms: int = 20) -> None:
        self.sample_rate = sample_rate
        self.block_frames = max(160, int(sample_rate * block_ms / 1000))
        self.block_ms = block_ms
        self._stream: Any | None = None
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[bytes]]] = set()
        self._state = "idle"
        self._error = ""
        self._error_code = ""
        self._endpoint: InputEndpoint | None = None
        self._candidates: list[InputEndpoint] = []
        self._candidate_cursor = 0
        self._started_at = 0.0
        self._first_pcm_at = 0.0
        self._last_pcm_at = 0.0
        self._frames = 0
        self._overflow_blocks = 0
        self._restart_count = 0
        self._last_restart_reason = ""
        self._ready_recovery_used = False

    @staticmethod
    def _offer(queue: asyncio.Queue[bytes], pcm: bytes) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(pcm)
        except asyncio.QueueFull:
            pass

    @staticmethod
    def _resample_pcm16_mono(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
        """Resample fixed-size PortAudio blocks without another runtime dependency.

        Mindspace uses 20 ms blocks.  The supported Windows endpoint rates make
        those blocks land on an integral number of 16 kHz frames (48k→16k and
        44.1k→16k), so deterministic nearest-neighbour decimation preserves
        block timing and avoids importing a second audio stack into ASR.
        """

        if source_rate == target_rate:
            return pcm
        source = array("h")
        source.frombytes(pcm)
        output_frames = max(1, round(len(source) * target_rate / source_rate))
        output = array(
            "h",
            (source[min(len(source) - 1, (index * source_rate) // target_rate)] for index in range(output_frames)),
        )
        return output.tobytes()

    @staticmethod
    def _endpoint_state_path() -> Path:
        root = Path(os.environ.get("MINDSPACE_DATA_ROOT") or os.environ.get("MINDSPACE_HOME") or ".")
        return root / "state" / "voice-capture-endpoint.json"

    def _read_remembered_endpoint(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self._endpoint_state_path().read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def _remember_endpoint(self) -> None:
        if self._endpoint is None:
            return
        path = self._endpoint_state_path()
        temporary = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(asdict(self._endpoint), ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _open_current_candidate(self, sounddevice: Any) -> bool:
        while self._candidate_cursor < len(self._candidates):
            endpoint = self._candidates[self._candidate_cursor]
            try:
                self._endpoint = endpoint
                self._stream = sounddevice.RawInputStream(
                    device=endpoint.device_index,
                    samplerate=endpoint.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=max(160, int(endpoint.sample_rate * self.block_ms / 1000)),
                    latency="low",
                    callback=self._callback,
                )
                self._stream.start()
                return True
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
                stream, self._stream = self._stream, None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._candidate_cursor += 1
        self._endpoint = None
        self._state = "error"
        self._error_code = "open_failed"
        return False

    def _callback(self, indata: Any, frames: int, _time_info: Any, status: Any) -> None:
        pcm = bytes(indata)
        endpoint = self._endpoint
        if not pcm or endpoint is None:
            return
        if endpoint.sample_rate != self.sample_rate:
            try:
                pcm = self._resample_pcm16_mono(pcm, endpoint.sample_rate, self.sample_rate)
            except (OverflowError, ValueError):
                return
        if not pcm:
            return
        now = time.perf_counter()
        if not self._first_pcm_at:
            self._first_pcm_at = now
            self._state = "ready"
            self._ready_recovery_used = False
            self._remember_endpoint()
        self._last_pcm_at = now
        self._frames += frames
        if status:
            self._overflow_blocks += 1
        with self._lock:
            subscribers = tuple(self._subscribers)
        for loop, queue in subscribers:
            if not loop.is_closed():
                loop.call_soon_threadsafe(self._offer, queue, pcm)

    def start(self) -> None:
        if os.environ.get("MINDSPACE_ASR_NATIVE_CAPTURE", "1") != "1":
            self._state = "disabled"
            return
        if self._stream is not None:
            return
        self._state = "opening"
        self._error = ""
        self._error_code = ""
        self._started_at = time.perf_counter()
        self._first_pcm_at = 0.0
        self._last_pcm_at = 0.0
        try:
            import sounddevice  # type: ignore[import-not-found]

            self._candidates = select_input_endpoints(sounddevice, self._read_remembered_endpoint())
            self._candidate_cursor = min(self._candidate_cursor, len(self._candidates) - 1)
            self._open_current_candidate(sounddevice)
        except Exception as exc:  # noqa: BLE001
            self._state = "error"
            self._error = str(exc)
            self._error_code = "no_input_device" if "未检测到可用的麦克风输入端点" in self._error else "open_failed"

    def stop(self, *, clear_subscribers: bool = True) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        if clear_subscribers:
            with self._lock:
                self._subscribers.clear()
        if self._state not in {"disabled", "error"}:
            self._state = "stopped"

    def restart_if_stalled(
        self,
        *,
        ready_timeout_seconds: float = 5.0,
        opening_timeout_seconds: float = 3.0,
    ) -> bool:
        """Advance one endpoint or perform one bounded ready-stream recovery."""

        now = time.perf_counter()
        opening_stalled = (
            self._state == "opening" and self._started_at and now - self._started_at >= opening_timeout_seconds
        )
        ready_stalled = (
            self._state == "ready" and self._last_pcm_at and now - self._last_pcm_at >= ready_timeout_seconds
        )
        if not (opening_stalled or ready_stalled):
            return False
        if ready_stalled and self._ready_recovery_used:
            self._state = "error"
            self._error_code = "capture_stalled"
            self._error = "麦克风在一次受控重连后仍未返回音频"
            self._last_restart_reason = "ready_stream_stalled"
            return False
        if opening_stalled:
            self._candidate_cursor += 1
            self._last_restart_reason = "first_pcm_timeout"
            if self._candidate_cursor >= len(self._candidates):
                self._state = "error"
                self._error_code = "first_pcm_timeout"
                self._error = "所有本机麦克风端点均未在首帧时限内返回音频"
                return False
        else:
            self._ready_recovery_used = True
            self._last_restart_reason = "ready_stream_stalled"
        self.stop(clear_subscribers=False)
        self._restart_count += 1
        self.start()
        return True

    def subscribe(self, loop: asyncio.AbstractEventLoop, *, max_blocks: int = 50) -> asyncio.Queue[bytes] | None:
        if self._state not in {"opening", "ready"}:
            return None
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_blocks)
        with self._lock:
            self._subscribers.add((loop, queue))
        return queue

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[bytes]) -> None:
        with self._lock:
            self._subscribers.discard((loop, queue))

    def status(self) -> dict[str, Any]:
        first_pcm_ms = (
            round((self._first_pcm_at - self._started_at) * 1000) if self._first_pcm_at and self._started_at else 0
        )
        last_pcm_age_ms = round((time.perf_counter() - self._last_pcm_at) * 1000) if self._last_pcm_at else 0
        with self._lock:
            subscribers = len(self._subscribers)
        endpoint = self._endpoint
        return {
            "enabled": self._state != "disabled",
            "available": self._state in {"opening", "ready"},
            "ready": self._state == "ready",
            "state": self._state,
            "device_index": endpoint.device_index if endpoint else -1,
            "device_name": endpoint.device_name if endpoint else "",
            "capture_endpoint": asdict(endpoint) if endpoint else {},
            "capture_state": self._state,
            "sample_rate": self.sample_rate,
            "source_sample_rate": endpoint.sample_rate if endpoint else 0,
            "block_frames": self.block_frames,
            "first_pcm_ms": first_pcm_ms,
            "last_pcm_age_ms": last_pcm_age_ms,
            "frames": self._frames,
            "overflow_blocks": self._overflow_blocks,
            "restart_count": self._restart_count,
            "last_restart_reason": self._last_restart_reason,
            "subscribers": subscribers,
            "error_code": self._error_code,
            "error": self._error,
        }
