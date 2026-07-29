import asyncio

import pytest

from mindspace_graph.audio import (
    AudioProviderUnavailable,
    AudioService,
    is_speakable_tts_text,
    qwen3_request_seed,
    sanitize_tts_text,
)
from mindspace_graph.settings import AppSettings


def test_tts_text_removes_nested_parenthetical_directions():
    assert sanitize_tts_text("（轻轻靠近（停顿））你好。(低声)今天好吗？") == "你好。今天好吗？"


def test_tts_text_drops_unclosed_parenthetical_tail():
    assert sanitize_tts_text("这句要读。（后面的动作没有闭合") == "这句要读。"


def test_tts_text_rejects_punctuation_only_fragments():
    assert is_speakable_tts_text("…… —— ♡") is False
    assert is_speakable_tts_text("……后面还有正文") is True


def test_streaming_tts_rejects_punctuation_before_opening_worker_stream(tmp_path):
    service = AudioService(AppSettings(runtime_dir=tmp_path, tts_provider="gpt-sovits"))
    with pytest.raises(AudioProviderUnavailable, match="没有可朗读"):
        asyncio.run(service.stream_synthesize("……", request_id="punctuation-only"))
    asyncio.run(service.aclose())


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


def test_qwen3_custom_voice_payload_locks_speaker_seed_and_one_style_instruction(
    tmp_path,
):
    async def exercise():
        class FakeResponse:
            is_error = False

            async def aiter_raw(self):
                yield b"\x00\x01"
                yield b"\x02\x03"

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_args):
                return None

        class FakeHttp:
            def __init__(self):
                self.request = None

            def stream(self, method, url, **kwargs):
                self.request = {"method": method, "url": url, **kwargs}
                return FakeStream()

            async def aclose(self):
                return None

        service = AudioService(AppSettings(
            runtime_dir=tmp_path,
            tts_provider="qwen3-vllm",
            tts_qwen3_vllm_url="http://127.0.0.1:8091",
            tts_qwen3_vllm_model="mindspace-qwen3-tts",
            tts_qwen3_vllm_voice="serena",
            tts_qwen3_vllm_task_type="CustomVoice",
        ))
        await service._http.aclose()
        fake_http = FakeHttp()
        service._http = fake_http
        stream, sample_rate = await service.stream_synthesize(
            "呵……你好，今天怎么样？",
            request_id="qwen-1",
            speed=0.9,
            voice_cue="neutral",
        )
        assert [chunk async for chunk in stream] == [b"\x00\x01", b"\x02\x03"]
        assert sample_rate == 24_000
        assert fake_http.request["method"] == "POST"
        assert fake_http.request["url"] == "http://127.0.0.1:8091/v1/audio/speech"
        assert fake_http.request["json"] == {
            "model": "mindspace-qwen3-tts",
            "input": "呵……你好，今天怎么样？",
            "voice": "serena",
            "seed": qwen3_request_seed("qwen-1", "serena"),
            "instructions": "语速舒缓偏慢，在合适处带一次短促、很轻的笑声，再自然接着说。",
            "response_format": "pcm",
            "stream": True,
            "stream_format": "audio",
            "task_type": "CustomVoice",
            "language": "Chinese",
            "non_streaming_mode": True,
            "max_new_tokens": 4096,
        }
        assert "speed" not in fake_http.request["json"]

    asyncio.run(exercise())


def test_qwen3_seed_is_stable_per_reply_and_voice():
    assert qwen3_request_seed("same-run", "vivian") == qwen3_request_seed("same-run", "vivian")
    assert qwen3_request_seed("same-run", "vivian") == qwen3_request_seed("other-run", "vivian")
    assert qwen3_request_seed("same-run", "vivian") != qwen3_request_seed("same-run", "ryan")


def test_qwen3_requests_use_the_same_single_synthesis_queue(tmp_path):
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        class FakeResponse:
            is_error = False

            async def aiter_raw(self):
                entered.set()
                await release.wait()
                yield b"\x00\x01"

        class FakeStream:
            def __init__(self, client):
                self.client = client

            async def __aenter__(self):
                self.client.active += 1
                self.client.maximum = max(self.client.maximum, self.client.active)
                return FakeResponse()

            async def __aexit__(self, *_args):
                self.client.active -= 1

        class FakeHttp:
            active = 0
            maximum = 0

            def stream(self, *_args, **_kwargs):
                return FakeStream(self)

            async def aclose(self):
                return None

        service = AudioService(AppSettings(runtime_dir=tmp_path, tts_provider="qwen3-vllm"))
        await service._http.aclose()
        service._http = FakeHttp()
        first, _ = await service.stream_synthesize("第一段。", request_id="qwen-1")
        second, _ = await service.stream_synthesize("第二段。", request_id="qwen-2")

        async def consume(stream):
            return [chunk async for chunk in stream]

        first_task = asyncio.create_task(consume(first))
        await asyncio.wait_for(entered.wait(), timeout=1)
        second_task = asyncio.create_task(consume(second))
        await asyncio.sleep(0)
        assert service._http.maximum == 1
        assert service._local_tts_waiters == 1
        release.set()
        await asyncio.gather(first_task, second_task)
        assert service._http.maximum == 1

    asyncio.run(exercise())
