from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.api import create_app
from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.characters import CharacterRepository, MIGRATION_KEY
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.settings import AppSettings


def make_settings(tmp_path):
    return AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
    )


def draft_payload(name: str = "林澈") -> dict:
    return {
        "ai_name": name,
        "ai_gender": "女",
        "core_traits": ["温柔", "坦率"],
        "flaw": "有些固执",
        "relationship": "朋友",
        "user_name": "测试用户",
        "user_alias": "用户",
    }


def test_legacy_profile_migration_is_idempotent_and_binds_sessions(tmp_path):
    settings = make_settings(tmp_path)
    legacy = deepcopy(DEFAULT_PROFILES["ai_profile"])
    legacy["identity"]["name"] = "旧角色"
    legacy["revision"] = 3
    profile_path = settings.runtime_dir / "data" / "profiles" / "ai-profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    app = create_app(settings)
    client = TestClient(app)

    first = client.get("/api/v1/characters").json()["items"]
    second_app = create_app(settings)
    second = TestClient(second_app).get("/api/v1/characters").json()["items"]

    assert len(first) == 1
    assert len(second) == 1
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

    database = ProductDatabase(
        settings.runtime_dir / "data" / "context" / "context.db"
    )
    assert database.has_document(MIGRATION_KEY) is False
    assert database.list_documents("character:") == []
    assert legacy_path.exists()
    assert (
        settings.runtime_dir
        / "data"
        / "backups"
        / "character-migration-0.6.0"
        / "manifest.json"
    ).exists()


def test_draw_draft_uses_at_most_one_model_call_and_commits(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    draft = client.post("/api/v1/character-drafts", json=draft_payload()).json()
    generated = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/generate"
    ).json()
    result = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/commit",
        json={"profile": generated["profile"]},
    )

    assert generated["model_call_count"] <= 1
    assert generated["generation_mode"] in {"llm", "local_template"}
    assert result.status_code == 200
    character = result.json()["character"]
    assert character["source"] == "draw"
    assert character["display_name"] == "林澈"
    assert character["gender"] == "女"


def test_session_character_binding_rejects_silent_rebind(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    original = client.get("/api/v1/characters").json()["items"][0]

    draft = client.post(
        "/api/v1/character-drafts", json=draft_payload("第二角色")
    ).json()
    created = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/commit"
    ).json()["character"]
    session_id = "role-bound-session"
    assert client.post(
        "/api/v1/sessions",
        json={"session_id": session_id, "character_id": original["character_id"]},
    ).status_code == 200

    response = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id, "character_id": created["character_id"]},
    )
    assert response.status_code == 409


def test_card_export_import_generates_new_id_and_excludes_private_data(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    draft = client.post("/api/v1/character-drafts", json=draft_payload()).json()
    character = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/commit"
    ).json()["character"]

    exported = client.get(
        f"/api/v1/characters/{character['character_id']}/export"
    )
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert set(archive.namelist()) <= {
            "manifest.json",
            "ai-profile.json",
            "avatar.webp",
            "avatar.png",
            "avatar.jpg",
            "avatar.gif",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "mindspace-card"
        assert "user_profile" not in json.dumps(manifest)
        assert "api_key" not in json.dumps(manifest)

    imported = client.post(
        "/api/v1/characters/import",
        files={
            "file": (
                "card.mindspace-card",
                exported.content,
                "application/vnd.mindspace.character+zip",
            )
        },
    )
    assert imported.status_code == 200
    assert imported.json()["character"]["character_id"] != character["character_id"]


def test_card_import_rejects_zip_path_traversal(tmp_path):
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


def test_historical_chat_retrieval_is_scoped_to_character(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    first = client.get("/api/v1/characters").json()["items"][0]
    draft = client.post(
        "/api/v1/character-drafts", json=draft_payload("隔离角色")
    ).json()
    second = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/commit"
    ).json()["character"]
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
