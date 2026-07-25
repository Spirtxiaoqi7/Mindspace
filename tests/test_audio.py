import asyncio

import pytest

from mindspace_graph.audio import AudioProviderUnavailable, AudioService, sanitize_tts_text
from mindspace_graph.settings import AppSettings


def test_tts_text_removes_nested_parenthetical_directions():
    assert sanitize_tts_text("（轻轻靠近（停顿））你好。(低声)今天好吗？") == "你好。今天好吗？"


def test_tts_text_drops_unclosed_parenthetical_tail():
    assert sanitize_tts_text("这句要读。（后面的动作没有闭合") == "这句要读。"


def test_siliconflow_payload_uses_raw_streaming_pcm(tmp_path):
    service = AudioService(
        AppSettings(
            runtime_dir=tmp_path,
            tts_provider="siliconflow",
            tts_siliconflow_api_key="secret",
            tts_siliconflow_model="fnlp/MOSS-TTSD-v0.5",
            tts_siliconflow_voice="fnlp/MOSS-TTSD-v0.5:alex",
            tts_siliconflow_sample_rate=24000,
            tts_siliconflow_gain=1.5,
        )
    )

    payload = service._siliconflow_payload("你好。", 1.25)

    assert payload == {
        "model": "fnlp/MOSS-TTSD-v0.5",
        "input": "你好。",
        "voice": "fnlp/MOSS-TTSD-v0.5:alex",
        "response_format": "pcm",
        "sample_rate": 24000,
        "stream": True,
        "speed": 1.25,
        "gain": 1.5,
    }
    assert "secret" not in repr(payload)


def test_siliconflow_payload_requires_api_key(tmp_path):
    service = AudioService(AppSettings(runtime_dir=tmp_path, tts_provider="siliconflow"))

    with pytest.raises(AudioProviderUnavailable, match="API 密钥"):
        service._siliconflow_payload("你好。", 1)


def test_local_tts_requests_are_serialized_before_reaching_worker(tmp_path):
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        class FakeResponse:
            def raise_for_status(self):
                return None

            async def aiter_bytes(self, chunk_size):
                assert chunk_size == 16_384
                entered.set()
                await release.wait()
                yield b"\x00\x01"

        class FakeStream:
            def __init__(self, client):
                self.client = client

            async def __aenter__(self):
                self.client.active += 1
                self.client.maximum = max(self.client.maximum, self.client.active)
                self.client.entries += 1
                return FakeResponse()

            async def __aexit__(self, *_args):
                self.client.active -= 1

        class FakeHttp:
            active = 0
            maximum = 0
            entries = 0

            def stream(self, *_args, **_kwargs):
                return FakeStream(self)

            async def aclose(self):
                return None

        service = AudioService(
            AppSettings(runtime_dir=tmp_path, tts_provider="gpt-sovits")
        )
        await service._http.aclose()
        service._http = FakeHttp()
        first, _ = await service.stream_synthesize("第一段。", request_id="same-run")
        second, _ = await service.stream_synthesize("第二段。", request_id="same-run")

        async def consume(stream):
            return [chunk async for chunk in stream]

        first_task = asyncio.create_task(consume(first))
        await asyncio.wait_for(entered.wait(), timeout=1)
        second_task = asyncio.create_task(consume(second))
        await asyncio.sleep(0)

        assert service._http.entries == 1
        assert service._local_tts_waiters == 1
        assert len(service._tasks["same-run"]) == 2

        release.set()
        assert await asyncio.gather(first_task, second_task) == [
            [b"\x00\x01"],
            [b"\x00\x01"],
        ]
        assert service._http.maximum == 1
        assert "same-run" not in service._tasks

    asyncio.run(exercise())
