from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.api import _voice_energy_threshold_db, create_app
from mindspace_graph.models import ChatRequest, JsonPatch, JsonUpdatePlan
from mindspace_graph.product_config import ProductConfigStore
from mindspace_graph.settings import AppSettings


def make_settings(tmp_path, **overrides):
    values = {
        "runtime_dir": tmp_path / "runtime",
        "llm_mode": "demo",
        "tts_provider": "browser",
        "asr_provider": "browser",
    }
    values.update(overrides)
    return AppSettings(**values)


def test_legacy_system_theme_migrates_to_mindscape(tmp_path):
    settings = make_settings(tmp_path)
    path = settings.runtime_dir / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"appearance": {"theme": "system"}}), encoding="utf-8")

    store = ProductConfigStore(path, settings)

    assert store.snapshot()["appearance"]["theme"] == "mindscape"
    assert json.loads(path.read_text(encoding="utf-8"))["appearance"]["theme"] == "mindscape"


def test_persisted_demo_mode_migrates_to_real_llm_provider(tmp_path):
    settings = make_settings(tmp_path)
    path = settings.runtime_dir / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"llm": {"mode": "demo", "api_key": "secret", "model": "deepseek-chat"}}),
        encoding="utf-8",
    )

    store = ProductConfigStore(path, settings)

    assert store.snapshot()["llm"]["mode"] == "openai"
    assert settings.llm_mode == "openai"
    assert json.loads(path.read_text(encoding="utf-8"))["llm"]["mode"] == "openai"


def test_appearance_font_scale_defaults_and_is_clamped(tmp_path):
    settings = make_settings(tmp_path)
    path = settings.runtime_dir / "config" / "settings.json"
    store = ProductConfigStore(path, settings)

    assert store.snapshot()["appearance"]["font_scale"] == 1.3
    updated = store.update({"appearance": {"font_scale": 9}})
    assert updated["appearance"]["font_scale"] == 1.6


def test_voice_phase_thresholds_and_idle_continuation_settings_are_migrated_and_clamped(
    tmp_path,
):
    settings = make_settings(tmp_path)
    path = settings.runtime_dir / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"audio": {"asr_min_speech_ms": 120}}), encoding="utf-8")

    store = ProductConfigStore(path, settings)
    snapshot = store.snapshot()

    assert snapshot["audio"]["asr_listening_min_speech_ms"] == 160
    assert snapshot["audio"]["asr_barge_in_min_speech_ms"] == 420
    assert snapshot["audio"]["asr_adaptive_noise_enabled"] is True
    assert snapshot["audio"]["asr_utterance_merge_ms"] == 350
    assert snapshot["audio"]["asr_silence_ms"] == 600
    assert snapshot["audio"]["asr_dynamic_endpointing"] is True
    assert snapshot["audio"]["asr_final_refinement_enabled"] is True
    assert snapshot["interaction"]["idle_continuation_enabled"] is False
    assert snapshot["interaction"]["voice_entry_mode"] == "call"
    assert snapshot["interaction"]["face_to_face_scene"] == ""
    assert snapshot["interaction"]["unlimited_reply_enabled"] is False
    assert snapshot["interaction"]["unlimited_reply_interval_seconds"] == 10
    assert snapshot["interaction"]["unlimited_reply_max_rounds"] == 10
    updated = store.update(
        {
            "interaction": {
                "text_idle_seconds": 2,
                "voice_idle_seconds": 9999,
                "unlimited_reply_enabled": True,
                "unlimited_reply_interval_seconds": 99,
                "unlimited_reply_max_rounds": 999,
            },
            "audio": {
                "asr_listening_min_speech_ms": 1,
                "asr_barge_in_min_speech_ms": 9999,
            },
        }
    )
    assert updated["interaction"]["text_idle_seconds"] == 10
    assert updated["interaction"]["voice_idle_seconds"] == 600
    assert updated["interaction"]["unlimited_reply_enabled"] is True
    assert updated["interaction"]["unlimited_reply_interval_seconds"] == 10
    assert updated["interaction"]["unlimited_reply_max_rounds"] == 50
    assert updated["audio"]["asr_listening_min_speech_ms"] == 60
    assert updated["audio"]["asr_barge_in_min_speech_ms"] == 1500


def test_legacy_fixed_endpoint_migrates_without_overwriting_custom_value(tmp_path):
    settings = make_settings(tmp_path)
    legacy = settings.runtime_dir / "legacy.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"schema_version": "1.0.0", "audio": {"asr_silence_ms": 250}}),
        encoding="utf-8",
    )
    custom = settings.runtime_dir / "custom.json"
    custom.write_text(
        json.dumps({"schema_version": "1.0.0", "audio": {"asr_silence_ms": 720}}),
        encoding="utf-8",
    )

    legacy_snapshot = ProductConfigStore(legacy, settings).snapshot()
    custom_snapshot = ProductConfigStore(custom, settings).snapshot()

    assert legacy_snapshot["schema_version"] == "1.2.0"
    assert legacy_snapshot["audio"]["asr_silence_ms"] == 600
    assert custom_snapshot["schema_version"] == "1.2.0"
    assert custom_snapshot["audio"]["asr_silence_ms"] == 720


def test_voice_entry_mode_and_scene_are_validated_and_persisted(tmp_path):
    settings = make_settings(tmp_path)
    path = settings.runtime_dir / "config" / "settings.json"
    store = ProductConfigStore(path, settings)

    updated = store.update(
        {
            "interaction": {
                "voice_entry_mode": "face_to_face",
                "face_to_face_scene": "  深夜客厅，窗外正在下雨。  ",
            }
        }
    )

    assert updated["interaction"]["voice_entry_mode"] == "face_to_face"
    assert updated["interaction"]["face_to_face_scene"] == "深夜客厅，窗外正在下雨。"
    restored = ProductConfigStore(path, settings).snapshot()
    assert restored["interaction"]["voice_entry_mode"] == "face_to_face"
    assert restored["interaction"]["face_to_face_scene"] == "深夜客厅，窗外正在下雨。"

    with pytest.raises(ValueError, match="voice_entry_mode"):
        store.update({"interaction": {"voice_entry_mode": "telepathy"}})


def test_playback_candidate_gate_does_not_double_apply_frontend_noise_margin(tmp_path):
    store = ProductConfigStore(tmp_path / "settings.json", make_settings(tmp_path))
    audio = store.snapshot()["audio"]
    audio["asr_barge_in_energy_threshold_db"] = -30
    audio["asr_barge_in_noise_margin_db"] = 16

    threshold = _voice_energy_threshold_db(
        audio,
        playing=True,
        noise_floor_db=-40,
    )

    assert threshold == -32
    assert threshold < -24  # the former double gate was too strict for ordinary speech


def test_asr_vocabulary_api_supports_live_edit_and_test(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    initial = client.get("/api/v1/audio/asr/vocabulary")
    assert initial.status_code == 200
    assert initial.json()["counts"]["system"] > 0

    updated = client.put(
        "/api/v1/audio/asr/vocabulary",
        json={
            "entries": [
                {
                    "term": "长离",
                    "aliases": ["长利"],
                    "priority": "critical",
                    "enabled": True,
                }
            ]
        },
    )
    assert updated.status_code == 200
    assert updated.json()["counts"]["manual"] == 1

    tested = client.post(
        "/api/v1/audio/asr/vocabulary/test",
        json={"text": "切换成长利的声音"},
    )
    assert tested.json()["corrected_text"] == "切换成长离的声音"


def test_product_page_health_and_public_config(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    page = client.get("/")
    health = client.get("/api/v1/health")
    config = client.get("/api/v1/config")

    assert page.status_code == 200
    assert "Mindspace Graph" in page.text
    assert health.json()["ok"] is True
    assert config.json()["shortcuts"]["interrupt"] == "Escape"
    assert "api_key" not in config.text


def test_gpt_sovits_voice_catalog_and_pending_selection(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    catalog = client.get("/api/v1/audio/tts/voices")
    assert catalog.status_code == 200
    voices = catalog.json()["items"]
    assert len(voices) == 48
    assert voices[0]["label"] == "V4-爱莉希雅（2026）"
    assert sum(item["family"] == "v4" for item in voices) == 38
    assert sum(item["family"] == "v2ProPlus" for item in voices) == 10
    assert all(item["installed"] is False for item in voices)

    selected = client.post(
        "/api/v1/audio/tts/voice/select",
        json={"voice_id": "v4-yae-miko"},
    )
    assert selected.status_code == 200
    assert selected.json()["ok"] is True
    assert selected.json()["pending_worker"] is True
    settings = client.get("/api/v1/settings").json()
    assert settings["audio"]["tts_provider"] == "gpt-sovits"
    assert settings["audio"]["tts_gpt_sovits_voice"] == "v4-yae-miko"


def test_llm_self_test_performs_a_real_minimal_generation(tmp_path, monkeypatch):
    observed = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

    class AsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aclose(self):
            return None

        async def post(self, url, **kwargs):
            observed.update({"url": url, **kwargs})
            return Response()

    monkeypatch.setattr("mindspace_graph.api.httpx.AsyncClient", AsyncClient)
    settings = make_settings(
        tmp_path,
        llm_mode="openai",
        llm_api_key="secret",
        llm_base_url="https://llm.example/v1",
        llm_model="role-model",
    )
    client = TestClient(create_app(settings))

    response = client.post("/api/v1/settings/test")

    assert response.json()["ok"] is True
    assert observed["url"] == "https://llm.example/v1/chat/completions"
    assert observed["json"]["model"] == "role-model"
    assert observed["json"]["max_tokens"] == 2


def test_stream_chat_emits_progress_final_and_persists_session(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    payload = {
        "message": "请解释服务调度",
        "session_id": "integration-session",
        "round": 1,
        "retrieval": {"similarity_threshold": 0},
    }

    response = client.post(
        "/api/v1/chat/stream",
        json=payload,
        headers={"X-Request-ID": "req-integration"},
    )

    assert response.status_code == 200
    assert "event: run.accepted" in response.text
    assert "event: node.started" in response.text
    assert "event: response.delta" in response.text
    assert "event: run.completed" in response.text
    envelopes = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [item["seq"] for item in envelopes] == list(range(1, len(envelopes) + 1))
    assert all(item["run_id"] == "req-integration" for item in envelopes)
    session = client.get("/api/v1/sessions/integration-session").json()
    assert len(session["messages"]) == 2
    assert session["messages"][1]["role"] == "assistant"
    assert "analysis" not in session["messages"][1]

    context = client.get("/api/v1/sessions/integration-session/context-diagnostics")
    assert context.status_code == 200
    assert context.json()["initialized"] is True
    assert context.json()["active_epoch_id"] > 0
    assert context.json()["event_count"] >= 4

    memory = client.get("/api/v1/memory/structured")
    assert memory.status_code == 200
    assert memory.json()["stats"] == {
        "active": 0,
        "untagged": 1,
        "episodes": 1,
        "tombstones": 0,
    }
    assert memory.json()["active"] == []

    redacted = client.get("/api/v1/runs/req-integration/prompt-inspection")
    revealed = client.get(
        "/api/v1/runs/req-integration/prompt-inspection?reveal=true"
    )
    assert redacted.status_code == 200
    assert redacted.json()["revealed"] is False
    assert all(
        item["content"].startswith("[已脱敏：") for item in redacted.json()["layers"]
    )
    assert revealed.status_code == 200
    assert revealed.json()["sha256"] == redacted.json()["sha256"]
    assert any(
        "请解释服务调度" in item["content"] for item in revealed.json()["layers"]
    )


def test_completed_stream_can_resume_by_sequence_without_reexecuting(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    payload = {
        "message": "验证流恢复",
        "session_id": "resume-session",
        "round": 1,
        "retrieval": {"rag_enabled": False},
    }
    initial = client.post(
        "/api/v1/chat/stream",
        json=payload,
        headers={"X-Request-ID": "resume-run"},
    )
    envelopes = [
        json.loads(line.removeprefix("data: "))
        for line in initial.text.splitlines()
        if line.startswith("data: ")
    ]
    cursor = envelopes[-3]["seq"]

    resumed = client.get(
        f"/api/v1/runs/resume-run/stream?after={cursor}",
        headers={"Last-Event-ID": str(cursor)},
    )
    replayed = [
        json.loads(line.removeprefix("data: "))
        for line in resumed.text.splitlines()
        if line.startswith("data: ")
    ]

    assert resumed.status_code == 200
    assert [item["seq"] for item in replayed] == [
        item["seq"] for item in envelopes if item["seq"] > cursor
    ]
    assert replayed[-1]["event"] == "run.completed"
    assert client.get("/api/v1/runs/resume-run").json() == {
        "run_id": "resume-run",
        "completed": True,
        "terminal_event": "run.completed",
        "latest_seq": envelopes[-1]["seq"],
    }
    session = client.get("/api/v1/sessions/resume-session").json()
    assert [item["role"] for item in session["messages"]] == ["user", "assistant"]


def test_asr_uncertainty_is_prompt_only_and_never_persisted_as_user_fact(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat/stream",
        headers={"X-Request-ID": "asr-evidence-run"},
        json={
            "message": "我想找帮我配音",
            "session_id": "asr-evidence-session",
            "round": 1,
            "retrieval": {"rag_enabled": False},
            "input_evidence": {
                "asr": {
                    "quality": "uncertain",
                    "confirmed_text": "我想找帮我配音",
                    "uncertain_segments": [
                        {
                            "text": "阿斯塔利昂",
                            "reason": "stream_final_disagreement",
                        }
                    ],
                    "decision_reasons": ["vad_confirmed"],
                }
            },
        },
    )

    assert response.status_code == 200
    inspection = client.get(
        "/api/v1/runs/asr-evidence-run/prompt-inspection?reveal=true"
    ).json()
    assert any("阿斯塔利昂" in layer["content"] for layer in inspection["layers"])
    session = client.get("/api/v1/sessions/asr-evidence-session").json()
    assert session["messages"][0]["content"] == "我想找帮我配音"
    with app.state.container.database.connection() as database:
        uncertain_rows = database.execute(
            "SELECT COUNT(*) FROM context_events WHERE kind='asr_uncertain_evidence'"
        ).fetchone()[0]
        persisted_candidate = database.execute(
            "SELECT COUNT(*) FROM context_events WHERE content LIKE '%阿斯塔利昂%'"
        ).fetchone()[0]
    assert uncertain_rows == 0
    assert persisted_candidate == 0


def test_delete_reply_keeps_user_and_reconciles_on_next_successful_turn(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    payload = {
        "message": "请记住这句话",
        "session_id": "delete-session",
        "round": 1,
        "retrieval": {"similarity_threshold": 0},
    }
    assert client.post("/api/v1/chat/stream", json=payload).status_code == 200
    before_profiles = app.state.container.profiles.load_bundle().model_dump(mode="json")
    session = client.get("/api/v1/sessions/delete-session").json()
    assistant = next(item for item in session["messages"] if item["role"] == "assistant")

    deleted = client.delete(f"/api/v1/sessions/delete-session/messages/{assistant['message_id']}")

    assert deleted.status_code == 200
    assert deleted.json()["pending_json_reconciliation"] is True
    assert [
        item["role"] for item in client.get("/api/v1/sessions/delete-session").json()["messages"]
    ] == ["user"]
    assert app.state.container.profiles.load_bundle().model_dump(mode="json") == before_profiles
    assert len(app.state.container.sessions.load_pending_deletions("delete-session")) == 1
    assert all(
        item.text != assistant["content"]
        for item in app.state.container.knowledge.search_chat("LangGraph", "delete-session", 10)
    )

    payload.update({"message": "继续", "round": 2})
    assert client.post("/api/v1/chat/stream", json=payload).status_code == 200
    assert app.state.container.sessions.load_pending_deletions("delete-session") == []


def test_knowledge_api_is_immediately_searchable(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)

    created = client.post(
        "/api/v1/knowledge",
        json={"text": "LangGraph 使用节点和边组织有状态工作流。", "source": "测试资料"},
    )
    results = app.state.container.knowledge.search_knowledge("LangGraph 节点", 5)

    assert created.status_code == 200
    assert created.json()["count"] == 1
    assert results
    assert results[0].metadata["source"] == "测试资料"


def test_initiative_chat_returns_only_the_assistant_message_publicly(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    payload = {
        "message": "transport placeholder",
        "session_id": "initiative-api",
        "round": 1,
        "initiative": True,
        "user_name": "阿澈",
        "retrieval": {"rag_enabled": False},
    }

    streamed = client.post("/api/v1/chat/stream", json=payload)
    session = client.get("/api/v1/sessions/initiative-api").json()
    stored = app.state.container.sessions.load_session("initiative-api")["messages"]

    assert streamed.status_code == 200
    assert [item["role"] for item in session["messages"]] == ["assistant"]
    assert session["messages"][0]["kind"] == "initiative_response"
    assert stored[0]["content"] == "阿澈不想说什么，但是想让你说点什么。"
    assert stored[0]["hidden"] is True

    deleted = client.delete(
        f"/api/v1/sessions/initiative-api/messages/{session['messages'][0]['message_id']}"
    )
    assert deleted.json()["pending_json_reconciliation"] is False
    assert client.get("/api/v1/sessions/initiative-api").json()["messages"] == []


def test_memory_registry_and_user_memory_crud_are_executable(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    container = app.state.container
    request = ChatRequest(message="我喜欢草莓", session_id="memory-api", round=1)
    bundle = container.profiles.load_bundle()
    receipt = container.profiles.apply_json_update(
        JsonUpdatePlan(
            turn_id="round_1",
            base_revisions=bundle.revisions,
            trigger="current_user",
            patches=[
                JsonPatch(
                    target="user_profile",
                    op="add",
                    path="/stable_preferences/likes/-",
                    value="草莓",
                    evidence_ids=["current_user"],
                )
            ],
        ),
        request=request,
    )
    container.memory.record_turn(
        request,
        "我记住了。",
        persisted={"user_message_id": "u-memory", "assistant_message_id": "a-memory"},
        write_receipt=receipt,
    )

    registry = client.get("/api/v1/memory/registry")
    listed = client.get("/api/v1/memory/items")
    memory_key = listed.json()["items"][0]["memory_key"]
    updated = client.put(f"/api/v1/memory/items/{memory_key}", json={"value": "蓝莓"})
    updated_key = updated.json()["item"]["memory_key"]
    deleted = client.delete(f"/api/v1/memory/items/{updated_key}")
    history = client.get("/api/v1/memory/items?include_history=true")
    restored = client.post("/api/v1/memory/restore", json={"memory_key": updated_key})

    assert registry.status_code == 200
    assert len(registry.json()["fields"]) >= 40
    assert listed.json()["items"][0]["value"] == "草莓"
    assert updated.status_code == 200
    assert updated.json()["item"]["value"] == "蓝莓"
    assert deleted.status_code == 200
    assert any(item["status"] == "invalidated" for item in history.json()["items"])
    assert restored.status_code == 200
    assert restored.json()["item"]["value"] == "蓝莓"


def test_mock_audio_endpoints_are_executable(tmp_path):
    settings = make_settings(tmp_path, tts_provider="mock", asr_provider="mock")
    client = TestClient(create_app(settings))

    tts = client.post(
        "/api/v1/audio/tts",
        json={"text": "测试", "request_id": "audio-1", "speed": 1},
    )
    asr = client.post(
        "/api/v1/audio/asr",
        files={"audio_file": ("voice.webm", b"fake-audio", "audio/webm")},
        headers={"X-Request-ID": "audio-2"},
    )

    assert tts.status_code == 200
    assert tts.content.startswith(b"RIFF")
    assert not list((settings.runtime_dir / "data" / "audio").glob("*.wav"))
    assert asr.status_code == 200
    assert asr.json()["text"] == "这是一条测试语音"


def test_mock_streaming_tts_returns_raw_pcm_with_audio_metadata(tmp_path):
    settings = make_settings(tmp_path, tts_provider="mock", asr_provider="mock")
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/v1/audio/tts/stream",
        json={"text": "（轻声）测试流式语音", "request_id": "audio-stream-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["x-audio-format"] == "pcm_s16le"
    assert response.headers["x-audio-sample-rate"] == "16000"
    assert len(response.content) == 6_400


def test_settings_profiles_knowledge_and_destructive_confirmations(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings))

    saved = client.put(
        "/api/v1/settings",
        json={
            "llm": {"api_key": "secret", "temperature": 1.2},
            "persona": {"character_name": "弦月"},
        },
    )
    public = client.get("/api/v1/settings")
    assert saved.status_code == 200
    assert public.json()["persona"]["character_name"] == "弦月"
    assert "secret" not in public.text
    assert "api_key" not in public.text
    assert settings.llm_api_key == "secret"
    assert settings.llm_mode == "openai"

    preserved = client.put("/api/v1/settings", json={"llm": {"api_key": "", "temperature": 0.8}})
    assert preserved.status_code == 200
    assert settings.llm_api_key == "secret"

    invalid_mode = client.put("/api/v1/settings", json={"llm": {"mode": "opena1"}})
    assert invalid_mode.status_code == 422
    assert client.get("/api/v1/settings").json()["llm"]["mode"] == "openai"

    profile = client.get("/api/v1/profiles/user").json()
    profile["identity"]["preferred_name"] = "小林"
    profile["identity"]["gender"] = "女"
    updated = client.put("/api/v1/profiles/user", json=profile)
    assert updated.status_code == 200
    assert updated.json()["document"]["identity"]["preferred_name"] == "小林"
    assert updated.json()["document"]["identity"]["gender"] == "女"
    assert updated.json()["document"]["revision"] == profile["revision"] + 1

    stale = client.put("/api/v1/profiles/user", json=profile)
    assert stale.status_code == 422
    assert "stale revision" in stale.json()["detail"]

    history = client.get("/api/v1/profiles/user/history").json()["items"]
    assert history
    restored = client.post(
        "/api/v1/profiles/user/restore",
        json={
            "version_id": history[0]["version_id"],
            "expected_revision": updated.json()["document"]["revision"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["document"]["identity"]["preferred_name"] != "小林"
    assert (
        restored.json()["document"]["revision"]
        == updated.json()["document"]["revision"] + 1
    )

    added = client.post(
        "/api/v1/knowledge",
        json={"text": "第一段知识。\n\n第二段知识。", "source": "integration"},
    )
    chunk_id = added.json()["chunk_ids"][0]
    assert client.get("/api/v1/knowledge/stats").json()["chunks"] == 2
    assert client.delete(f"/api/v1/knowledge/{chunk_id}").status_code == 200
    denied = client.post(
        "/api/v1/data/clear",
        json={"scope": "knowledge", "confirmation": "wrong"},
    )
    assert denied.status_code == 422


def test_mock_realtime_asr_websocket_protocol(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path, asr_provider="mock")))
    with client.websocket_connect("/api/v1/audio/asr/stream") as websocket:
        assert websocket.receive_json()["event"] == "asr.ready"
        websocket.send_json({"action": "start"})
        websocket.send_bytes(b"\x00\x01" * 320)
        assert websocket.receive_json()["event"] == "asr.speech_start"
        assert websocket.receive_json()["event"] == "asr.partial"
        websocket.send_json({"action": "stop"})
        final = websocket.receive_json()
        assert final["event"] == "asr.final"
        assert final["data"]["auto_send"] is True


def test_tts_reference_upload_is_atomic_public_and_clearable(tmp_path):
    settings = make_settings(tmp_path, tts_provider="mock", asr_provider="mock")
    client = TestClient(create_app(settings))

    uploaded = client.post(
        "/api/v1/audio/tts/reference",
        files={"file": ("sample.wav", b"RIFF" + b"\x00" * 80, "audio/wav")},
        data={"transcript": "这是参考音频。"},
    )

    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["reference"]["configured"] is True
    assert body["reference"]["transcript"] == "这是参考音频。"
    public = client.get("/api/v1/settings").json()["audio"]
    assert public["tts_reference_configured"] is True
    assert public["tts_reference_name"].endswith(".wav")
    assert str(tmp_path) not in json.dumps(public)
    assert settings.tts_reference_text == "这是参考音频。"
    first_path = settings.tts_reference_audio

    replaced = client.post(
        "/api/v1/audio/tts/reference",
        files={"file": ("replacement.flac", b"fLaC" + b"\x00" * 80, "audio/flac")},
        data={"transcript": "替换后的参考音频。"},
    )

    assert replaced.status_code == 200
    assert not Path(first_path).exists()
    assert Path(settings.tts_reference_audio).suffix == ".flac"
    assert settings.tts_reference_text == "替换后的参考音频。"

    recognized = client.post("/api/v1/audio/tts/reference/transcribe")
    assert recognized.status_code == 200
    assert recognized.json()["transcript"] == "这是一条测试语音"
    assert recognized.json()["settings"]["tts_reference_text"] == "这是一条测试语音"
    assert settings.tts_reference_text == "这是一条测试语音"

    cleared = client.delete("/api/v1/audio/tts/reference")
    assert cleared.status_code == 200
    assert cleared.json()["reference"]["configured"] is False
    assert client.get("/api/v1/settings").json()["audio"]["tts_reference_configured"] is False
    missing = client.post("/api/v1/audio/tts/reference/transcribe")
    assert missing.status_code == 409

    unsupported = client.post(
        "/api/v1/audio/tts/reference",
        files={"file": ("sample.txt", b"not audio", "text/plain")},
    )
    assert unsupported.status_code == 422


def test_avatar_config_normalizes_legacy_values_and_upload_returns_config(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    config_path = settings.runtime_dir / "data" / "avatars" / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "user": {"src": "/legacy.webp", "shape": "circle", "scale": 99},
                "assistant": {"aspect": "invalid", "x": -999},
            }
        ),
        encoding="utf-8",
    )

    normalized = client.get("/api/v1/avatar/config").json()
    assert normalized["user"]["src"] == "/legacy.webp"
    assert normalized["user"]["scale"] == 3
    assert normalized["assistant"]["aspect"] == "2 / 3"
    assert normalized["assistant"]["x"] == -80
    assert "shape" not in normalized["user"]

    uploaded = client.post(
        "/api/v1/avatar/upload/assistant",
        files={"file": ("portrait.webp", b"RIFF" + b"\x00" * 16, "image/webp")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["config"]["assistant"]["src"].startswith(
        "/api/v1/avatar/files/assistant-"
    )
