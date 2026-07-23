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
