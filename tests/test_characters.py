from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.api import create_app
from mindspace_graph.characters import MIGRATION_KEY, CharacterRepository
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.settings import AppSettings


def make_settings(tmp_path):
    return AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
    )


def v2_card(name: str = "林澈") -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": f"{name}是一个重视事实与连续性的聊天角色。",
            "personality": "温柔、坦率，会表达自己的判断。",
            "scenario": "与用户在日常生活里持续相处。",
            "first_mes": "我在，今天想先聊什么？",
            "alternate_greetings": ["我在听。", "先说说你最在意的事。"],
            "mes_example": "{{user}} 我有点犹豫。\n{{char}} 那我们先把你不想失去的部分说清楚。",
            "extensions": {"mindspace": {"gender": "女", "relationship": "朋友"}},
        },
    }


def create_v2_character(client: TestClient, name: str = "林澈") -> dict:
    response = client.post(
        "/api/v1/characters",
        json={"source": "custom", "card": v2_card(name)},
    )
    assert response.status_code == 200
    return response.json()["character"]


def test_legacy_profile_migration_is_idempotent_and_binds_sessions(tmp_path):
    settings = make_settings(tmp_path)
    legacy = deepcopy(DEFAULT_PROFILES["ai_profile"])
    legacy["identity"]["name"] = "旧角色"
    legacy["revision"] = 3
    profile_path = settings.runtime_dir / "data" / "profiles" / "ai-profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    first = TestClient(create_app(settings)).get("/api/v1/characters").json()["items"]
    second = TestClient(create_app(settings)).get("/api/v1/characters").json()["items"]

    assert len(first) == len(second) == 1
    assert first[0]["character_id"] == second[0]["character_id"]
    assert first[0]["source"] == "migrated"


def test_character_migration_rolls_back_database_and_keeps_backup(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    legacy_path = settings.runtime_dir / "data" / "profiles" / "ai-profile.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(DEFAULT_PROFILES["ai_profile"], ensure_ascii=False),
        encoding="utf-8",
    )

    def fail_store(self, record):  # noqa: ARG001
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(CharacterRepository, "_store", fail_store)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        create_app(settings)

    database = ProductDatabase(settings.runtime_dir / "data" / "context" / "context.db")
    assert database.has_document(MIGRATION_KEY) is False
    assert database.list_documents("character:") == []
    assert legacy_path.exists()
    backup = settings.runtime_dir / "data" / "backups" / "character-migration-0.6.0"
    assert (backup / "manifest.json").exists()


def test_legacy_character_creation_routes_are_gone(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    for method, path in (
        ("GET", "/api/v1/characters/options"),
        ("POST", "/api/v1/characters/fate-options"),
        ("POST", "/api/v1/character-drafts"),
        ("GET", "/api/v1/character-drafts/legacy-id"),
        ("PUT", "/api/v1/character-drafts/legacy-id"),
        ("POST", "/api/v1/character-drafts/legacy-id/generate"),
        ("POST", "/api/v1/character-drafts/legacy-id/rewrite"),
        ("POST", "/api/v1/character-drafts/legacy-id/avatar"),
        ("POST", "/api/v1/character-drafts/legacy-id/commit"),
    ):
        response = client.request(method, path, json={})
        assert response.status_code == 410
        assert "废弃" in response.json()["detail"]


def test_v2_session_character_binding_rejects_silent_rebind(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    original = client.get("/api/v1/characters").json()["items"][0]
    created = create_v2_character(client, "第二角色")
    session_id = "role-bound-session"
    bound = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id, "character_id": original["character_id"]},
    )
    assert bound.status_code == 200
    response = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id, "character_id": created["character_id"]},
    )
    assert response.status_code == 409


def test_v2_card_export_import_generates_new_id_and_excludes_private_data(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    character = create_v2_character(client)
    exported = client.get(f"/api/v1/characters/{character['character_id']}/export")
    assert exported.status_code == 200
    exported_card = exported.json()
    assert exported_card["spec"] == "chara_card_v2"
    assert "api_key" not in json.dumps(exported_card)

    imported = client.post(
        "/api/v1/characters/import",
        files={"file": ("card.json", exported.content, "application/json")},
    )
    assert imported.status_code == 200
    assert imported.json()["character"]["character_id"] != character["character_id"]


def test_v2_legacy_task_titles_migrate_to_stable_structured_tasks(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    card = v2_card("任务迁移角色")
    card["data"]["memory"] = {"preferences": [], "tasks": ["交报告"]}
    created = client.post("/api/v1/characters", json={"source": "custom", "card": card}).json()["character"]

    tasks = created["card"]["data"]["extensions"]["mindspace"]["tasks_v2"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "交报告"
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["id"]
    exported = client.get(f"/api/v1/characters/{created['character_id']}/export").json()
    assert exported["data"]["memory"]["tasks"] == ["交报告"]
    assert exported["data"]["extensions"]["mindspace"]["tasks_v2"][0]["id"] == tasks[0]["id"]


def test_task_commands_are_revisioned_and_idempotent(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    character = create_v2_character(client, "任务角色")
    repository = app.state.container.characters
    revision = int(character["revision"])
    command = {"op": "create", "title": "交报告", "due_at": "2026-08-11T18:00:00+08:00"}

    created = repository.execute_task_command(
        character["character_id"],
        command,
        request_id="task-idempotency",
        command_hash="stable-command-hash",
        expected_revision=revision,
    )
    replay = repository.execute_task_command(
        character["character_id"],
        command,
        request_id="task-idempotency",
        command_hash="stable-command-hash",
        expected_revision=revision,
    )

    assert replay == created
    assert created["changed"] is True
    assert len(repository.get(character["character_id"])["card"]["data"]["extensions"]["mindspace"]["tasks_v2"]) == 1
    with pytest.raises(ValueError, match="stale character revision"):
        repository.execute_task_command(
            character["character_id"],
            {"op": "create", "title": "重复任务"},
            request_id="task-stale",
            command_hash="different-command-hash",
            expected_revision=revision,
        )


def test_v2_card_import_rejects_zip_path_traversal(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.json", "{}")
        archive.writestr("manifest.json", "{}")
        archive.writestr("ai-profile.json", "{}")

    response = client.post(
        "/api/v1/characters/import",
        files={"file": ("bad.mindspace-card", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 422


def test_historical_chat_retrieval_is_scoped_to_v2_character(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    first = client.get("/api/v1/characters").json()["items"][0]
    second = create_v2_character(client, "隔离角色")
    for session_id, character, phrase in (
        ("history-a", first, "青铜风铃只属于角色甲"),
        ("history-b", second, "紫色雨伞只属于角色乙"),
    ):
        client.post(
            "/api/v1/sessions",
            json={"session_id": session_id, "character_id": character["character_id"]},
        )
        response = client.post(
            "/api/v1/chat",
            json={
                "message": phrase,
                "session_id": session_id,
                "character_id": character["character_id"],
                "round": 1,
                "retrieval": {"rag_enabled": False},
            },
        )
        assert response.status_code == 200

    chunks = app.state.container.conversation.dependencies.retriever.search_chat(
        "只属于角色",
        "new-session-a",
        20,
        character_id=first["character_id"],
        messages=[],
    )
    joined = "\n".join(item.text for item in chunks)
    assert "青铜风铃只属于角色甲" in joined
    assert "紫色雨伞只属于角色乙" not in joined
