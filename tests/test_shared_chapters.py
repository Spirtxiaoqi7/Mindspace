from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.api import create_app
from mindspace_graph.models import ApiConfig, ChatRequest
from mindspace_graph.settings import AppSettings
from mindspace_graph.shared_chapters import ActivityAction, ActivityStart


def make_app(tmp_path):
    return create_app(
        AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
        )
    )


def test_journals_and_moments_are_character_isolated_and_narrative_only(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    first = container.characters.default()
    second = container.characters.clone(first["character_id"])
    before = deepcopy(container.characters.get(first["character_id"])["ai_profile"])

    with TestClient(app) as client:
        journal = client.post(
            f"/api/v1/characters/{first['character_id']}/journal",
            json={"title": "雨夜", "content": "我们聊到了窗外的雨。"},
        )
        assert journal.status_code == 200
        entry = journal.json()
        assert entry["visibility"] == "narrative_only"
        assert entry["eligible_for_json_evidence"] is False

        moment = client.post(
            f"/api/v1/characters/{first['character_id']}/moments",
            json={
                "title": "一起听雨",
                "summary": "用户确认收藏的片段。",
                "status": "saved",
            },
        )
        assert moment.status_code == 200
        assert moment.json()["eligible_for_json_evidence"] is False

        assert client.get(
            f"/api/v1/characters/{first['character_id']}/journal"
        ).json()["count"] == 1
        assert client.get(
            f"/api/v1/characters/{second['character_id']}/journal"
        ).json()["count"] == 0
        assert client.get(
            f"/api/v1/characters/{second['character_id']}/moments"
        ).json()["count"] == 0
        hits = container.chapters.search_narratives(
            first["character_id"], "窗外一起听雨"
        )
        assert hits
        assert all(item.metadata["visibility"] == "narrative_only" for item in hits)
        assert container.chapters.search_narratives(
            second["character_id"], "窗外一起听雨"
        ) == []

    after = container.characters.get(first["character_id"])["ai_profile"]
    assert after == before


def test_activity_actions_are_revisioned_idempotent_and_character_bound(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    first = container.characters.default()
    second = container.characters.clone(first["character_id"])
    scene_activity = container.chapters.activities()[0]
    assert len(scene_activity["scenes"]) == 20
    assert all(
        "-preview" not in scene["asset_id"] for scene in scene_activity["scenes"]
    )

    with TestClient(app) as client:
        started = client.post(
            "/api/v1/activities/scene_companion/sessions",
            json={"character_id": first["character_id"], "session_id": "activity-chat"},
        ).json()
        payload = {
            "action_id": "stable-action-1",
            "expected_revision": 1,
            "action": "select_scene",
            "payload": {"scene_id": "riverside_evening"},
        }
        applied = client.post(
            f"/api/v1/activity-sessions/{started['activity_session_id']}/actions",
            json=payload,
        )
        assert applied.status_code == 200
        assert applied.json()["session"]["revision"] == 2

        replay = client.post(
            f"/api/v1/activity-sessions/{started['activity_session_id']}/actions",
            json=payload,
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True

        interrupted = client.post(
            f"/api/v1/activity-sessions/{started['activity_session_id']}/actions",
            json={
                "action_id": "interrupt-action",
                "expected_revision": 2,
                "action": "cancel",
            },
        )
        assert interrupted.status_code == 200
        assert interrupted.json()["session"]["status"] == "interrupted"

        resumed = client.post(
            f"/api/v1/activity-sessions/{started['activity_session_id']}/actions",
            json={
                "action_id": "resume-action",
                "expected_revision": 3,
                "action": "resume",
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["session"]["phase"] == "conversation"

        conflict = client.post(
            f"/api/v1/activity-sessions/{started['activity_session_id']}/actions",
            json={
                "action_id": "stale-action",
                "expected_revision": 1,
                "action": "complete",
            },
        )
        assert conflict.status_code == 409

        with pytest.raises(ValueError, match="another character"):
            container.chapters.prompt_context(
                started["activity_session_id"],
                character_id=second["character_id"],
            )


def test_activity_context_is_resolved_server_side_for_chat(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    activity = container.chapters.start_activity(
        "mutual_questions",
        ActivityStart(
            character_id=character["character_id"],
            session_id="session-a",
        ),
    )
    request = ChatRequest(
        message="继续吧",
        session_id="session-a",
        character_id=character["character_id"],
        activity_session_id=activity["activity_session_id"],
        round=1,
        api=ApiConfig(),
    )
    resolved = container.conversation._server_request(request)
    assert resolved.activity_context is not None
    assert resolved.activity_context.visibility == "ephemeral_activity_session"
    assert resolved.activity_context.eligible_for_json_evidence is False
    container.conversation.close()
    container.art_catalog.close()


def test_prompt_inspector_marks_activity_as_ephemeral_system_layer(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    profile_before = deepcopy(
        container.characters.get(character["character_id"])["ai_profile"]
    )
    activity = container.chapters.start_activity(
        "scene_companion",
        ActivityStart(
            character_id=character["character_id"],
            session_id="prompt-activity-session",
        ),
    )
    container.chapters.apply_activity_action(
        activity["activity_session_id"],
        ActivityAction(
            action_id="choose-river",
            expected_revision=1,
            action="select_scene",
            payload={"scene_id": "riverside_evening"},
        ),
    )

    with TestClient(app) as client:
        streamed = client.post(
            "/api/v1/chat/stream",
            headers={"X-Request-ID": "activity-prompt-run"},
            json={
                "message": "风有点大。",
                "session_id": "prompt-activity-session",
                "character_id": character["character_id"],
                "activity_session_id": activity["activity_session_id"],
                "round": 1,
            },
        )
        assert streamed.status_code == 200
        inspection = client.get(
            "/api/v1/runs/activity-prompt-run/prompt-inspection?reveal=true"
        )
        assert inspection.status_code == 200
        layers = inspection.json()["layers"]
        assert any(item["layer"] == "activity_context" for item in layers)
        activity_layer = next(
            item for item in layers if item["layer"] == "activity_context"
        )
        assert activity_layer["role"] == "system"
        assert "不得自行推进阶段" in activity_layer["content"]
        assert container.chapters.list_journals(character["character_id"]) == []
        assert container.chapters.list_moments(character["character_id"]) == []
        assert (
            container.characters.get(character["character_id"])["ai_profile"]
            == profile_before
        )


def test_confirmed_continuity_migration_is_idempotent(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    character_id = character["character_id"]
    updated = deepcopy(character)
    updated["ai_profile"].setdefault("continuity", {})[
        "important_shared_experiences"
    ] = ["一起在雨夜听过窗外的雨。"]
    container.database.put_document(f"character:{character_id}", updated)

    marker = "migration:shared-chapters:0.7.0"
    container.database.delete_document(marker)
    container.chapters._migrate_confirmed_continuity()
    first_pass = container.chapters.list_moments(character_id)

    # Even if a repair removes the completion marker, stable source hashes must
    # prevent duplicate moments from being manufactured.
    container.database.delete_document(marker)
    container.chapters._migrate_confirmed_continuity()
    second_pass = container.chapters.list_moments(character_id)

    migrated = [
        item
        for item in second_pass
        if item["source"] == "profile_migration"
        and item["summary"] == "一起在雨夜听过窗外的雨。"
    ]
    assert len(migrated) == 1
    assert len(second_pass) == len(first_pass)
    container.conversation.close()
    container.art_catalog.close()
