from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from mindspace_graph.native_microphone import (
    NativeMicrophoneCapture,
    select_input_device,
    select_input_endpoints,
)


class FakeInputStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeInputOutputPair:
    def __init__(self, input_device: int, output_device: int) -> None:
        self._values = (input_device, output_device)

    def __getitem__(self, index: int) -> int:
        return self._values[index]


def fake_sounddevice() -> SimpleNamespace:
    streams: list[FakeInputStream] = []
    devices = [
        {"name": "speaker", "max_input_channels": 0},
        {"name": "microphone", "max_input_channels": 1},
        {"name": "fallback", "max_input_channels": 1},
    ]

    def raw_input_stream(**kwargs):
        stream = FakeInputStream(**kwargs)
        streams.append(stream)
        return stream

    return SimpleNamespace(
        __version__="0.5.5",
        default=SimpleNamespace(device=(2, -1)),
        query_hostapis=lambda: [
            {"name": "Windows WASAPI", "default_input_device": 3},
            {"name": "MME", "default_input_device": 1},
        ],
        query_devices=lambda index=None: devices if index is None else devices[index],
        RawInputStream=raw_input_stream,
        streams=streams,
    )


def test_select_input_device_prefers_windows_mme(monkeypatch) -> None:
    sounddevice = fake_sounddevice()
    monkeypatch.setattr(sys, "platform", "win32")

    assert select_input_device(sounddevice) == 1


def test_endpoint_selection_prefers_wasapi_native_rate_and_remembers_success(monkeypatch) -> None:
    sounddevice = fake_sounddevice()
    devices = list(sounddevice.query_devices())
    devices.extend(
        [
            {"name": "wasapi mic", "max_input_channels": 1, "hostapi": 2, "default_samplerate": 48_000},
            {"name": "wdm mic", "max_input_channels": 1, "hostapi": 3, "default_samplerate": 44_100},
        ]
    )
    sounddevice.query_devices = lambda index=None: devices if index is None else devices[index]
    sounddevice.query_hostapis = lambda: [
        {"name": "MME", "default_input_device": 1},
        {"name": "Windows DirectSound", "default_input_device": -1},
        {"name": "Windows WASAPI", "default_input_device": 3},
        {"name": "Windows WDM-KS", "default_input_device": 4},
    ]
    monkeypatch.setattr(sys, "platform", "win32")

    endpoints = select_input_endpoints(sounddevice)
    assert endpoints[0].hostapi == "Windows WASAPI"
    assert endpoints[0].sample_rate == 48_000
    remembered = select_input_endpoints(sounddevice, {
        "device_name": "wdm mic", "hostapi": "Windows WDM-KS", "sample_rate": 44_100,
    })
    assert remembered[0].hostapi == "Windows WDM-KS"


def test_native_capture_resamples_wasapi_block_to_asr_rate() -> None:
    source = b"\x01\x00" * 960  # 20 ms at 48 kHz
    result = NativeMicrophoneCapture._resample_pcm16_mono(source, 48_000, 16_000)

    assert len(result) == 320 * 2  # 20 ms at 16 kHz


def test_select_input_device_accepts_sounddevice_input_output_pair(monkeypatch) -> None:
    sounddevice = fake_sounddevice()
    sounddevice.query_hostapis = lambda: [
        {"name": "MME", "devices": [0], "default_input_device": -1},
    ]
    sounddevice.query_devices = lambda: [
        {"name": "speaker", "max_input_channels": 0},
        {"name": "microphone", "max_input_channels": 1},
    ]
    sounddevice.default.device = FakeInputOutputPair(-1, 1)
    monkeypatch.setattr(sys, "platform", "win32")

    assert select_input_device(sounddevice) == 1


def test_select_input_device_explains_when_windows_has_no_input(monkeypatch) -> None:
    sounddevice = fake_sounddevice()
    sounddevice.query_hostapis = lambda: [
        {"name": "MME", "devices": [0], "default_input_device": -1},
    ]
    sounddevice.query_devices = lambda: [{"name": "speaker", "max_input_channels": 0}]
    sounddevice.default.device = (-1, 1)
    monkeypatch.setattr(sys, "platform", "win32")

    try:
        select_input_device(sounddevice)
    except RuntimeError as exc:
        assert "HyperX Cloud III" in str(exc)
    else:
        raise AssertionError("expected a missing-input error")


def test_resident_capture_fans_out_pcm_and_stays_open(monkeypatch, tmp_path) -> None:
    sounddevice = fake_sounddevice()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setenv("MINDSPACE_DATA_ROOT", str(tmp_path))
    capture = NativeMicrophoneCapture(sample_rate=16_000, block_ms=20)
    loop = asyncio.new_event_loop()
    try:
        capture.start()
        queue = capture.subscribe(loop, max_blocks=2)
        assert queue is not None
        assert capture.status()["state"] == "opening"
        assert sounddevice.streams[0].started is True

        first = b"\x01\x00" * 320
        second = b"\x02\x00" * 320
        third = b"\x03\x00" * 320
        sounddevice.streams[0].callback(first, 320, None, None)
        sounddevice.streams[0].callback(second, 320, None, None)
        sounddevice.streams[0].callback(third, 320, None, None)
        loop.run_until_complete(asyncio.sleep(0))

        assert capture.status()["ready"] is True
        assert capture.status()["frames"] == 960
        assert queue.get_nowait() == second
        assert queue.get_nowait() == third
        capture._last_pcm_at = time.perf_counter() - 10
        assert capture.restart_if_stalled(ready_timeout_seconds=1) is True
        assert len(sounddevice.streams) == 2
        assert capture.status()["restart_count"] == 1
        assert capture.status()["subscribers"] == 1
        capture.unsubscribe(loop, queue)
        assert capture.status()["subscribers"] == 0

        capture.stop()
        assert sounddevice.streams[0].stopped is True
        assert sounddevice.streams[0].closed is True
    finally:
        loop.close()


def test_capture_disables_without_opening_device(monkeypatch) -> None:
    monkeypatch.setenv("MINDSPACE_ASR_NATIVE_CAPTURE", "0")
    capture = NativeMicrophoneCapture()

    capture.start()

    assert capture.status()["state"] == "disabled"
    assert capture.status()["available"] is False


def test_capture_reports_a_missing_windows_input_endpoint(monkeypatch) -> None:
    sounddevice = fake_sounddevice()
    sounddevice.query_hostapis = lambda: [
        {"name": "MME", "devices": [0], "default_input_device": -1},
    ]
    sounddevice.query_devices = lambda index=None: [
        {"name": "speaker", "max_input_channels": 0},
    ] if index is None else {"name": "speaker", "max_input_channels": 0}
    sounddevice.default.device = FakeInputOutputPair(-1, 0)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    capture = NativeMicrophoneCapture()

    capture.start()

    assert capture.status()["state"] == "error"
    assert capture.status()["error_code"] == "no_input_device"
