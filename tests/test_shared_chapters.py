from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.adapters.in_memory import InMemorySessionRepository
from mindspace_graph.api import create_app
from mindspace_graph.models import ApiConfig, ChatRequest
from mindspace_graph.settings import AppSettings
from mindspace_graph.shared_chapters import ActivityStart


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
    before = deepcopy(container.characters.get(first["character_id"])["card"])

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

        assert client.get(f"/api/v1/characters/{first['character_id']}/journal").json()["count"] == 1
        assert client.get(f"/api/v1/characters/{second['character_id']}/journal").json()["count"] == 0
        assert client.get(f"/api/v1/characters/{second['character_id']}/moments").json()["count"] == 0
        hits = container.chapters.search_narratives(first["character_id"], "窗外一起听雨")
        assert hits
        assert all(item.metadata["visibility"] == "narrative_only" for item in hits)
        assert container.chapters.search_narratives(second["character_id"], "窗外一起听雨") == []

    after = container.characters.get(first["character_id"])["card"]
    assert after == before


def test_journal_uses_character_first_person_and_complete_user_anchored_rounds(
    tmp_path,
):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    name = character["display_name"]
    sessions = InMemorySessionRepository(
        sessions={
            "journal-session": [
                {"role": "user", "content": "今天工作有点累。", "round": 1},
                {"role": "assistant", "content": "那就先歇一会儿。", "round": 1},
                {
                    "role": "assistant",
                    "content": "这是一条没有用户承接的自动主动消息。",
                    "round": 2,
                },
                {"role": "user", "content": "我想听你说说今天。", "round": 3},
                {"role": "assistant", "content": "我愿意写下来。", "round": 3},
            ]
        }
    )

    class RecordingLLM:
        def __init__(self):
            self.messages = []

        def generate(self, messages, _config):
            self.messages = messages
            return (
                "今天我听见你说工作有些累，我想让你先好好歇一会儿。"
                "后来你想听我说说今天，我答应把这些真实留下的话写下来。"
            )

    llm = RecordingLLM()
    container.chapters.sessions = sessions
    container.chapters.llm_provider = lambda: llm
    container.chapters.api_provider = lambda: ApiConfig()

    generated = container.chapters.generate_journal(character["character_id"], session_id="journal-session")

    assert generated["generation"] == "llm"
    assert generated["model_calls"] == 1
    assert generated["source_scope"] == {
        "session_id": "journal-session",
        "round_start": 1,
        "round_end": 3,
        "message_count": 4,
    }
    assert generated["entry"]["content"].startswith("今天我")
    prompt = "\n".join(item["content"] for item in llm.messages)
    assert f"{name}本人（日记作者）" in prompt
    assert "用户本人（不是日记作者）" in prompt
    assert "没有用户承接的自动主动消息" not in prompt
    assert generated["entry"]["source_round_start"] == 1
    assert generated["entry"]["source_round_end"] == 3

    container.conversation.close()
    container.art_catalog.close()


def test_journal_invalid_perspective_falls_back_but_counts_attempt(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    container.chapters.sessions = InMemorySessionRepository(
        sessions={
            "bad-journal": [
                {"role": "user", "content": "今天辛苦了。", "round": 1},
                {"role": "assistant", "content": "我陪着你。", "round": 1},
            ]
        }
    )

    class WrongPerspectiveLLM:
        def generate(self, _messages, _config):
            return "她今天陪着用户，觉得这是一段值得记录的对话。"

    container.chapters.llm_provider = lambda: WrongPerspectiveLLM()
    container.chapters.api_provider = lambda: ApiConfig()
    generated = container.chapters.generate_journal(character["character_id"], session_id="bad-journal")

    assert generated["generation"] == "template"
    assert generated["model_calls"] == 1
    assert "我和你" in generated["entry"]["content"]
    assert generated["entry"]["source_message_count"] == 2

    container.conversation.close()
    container.art_catalog.close()


def test_activity_actions_are_revisioned_idempotent_and_character_bound(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    first = container.characters.default()
    second = container.characters.clone(first["character_id"])
    assert {item["activity_id"] for item in container.chapters.activities()} == {
        "mutual_questions",
        "story_choices",
    }

    with TestClient(app) as client:
        started = client.post(
            "/api/v1/activities/mutual_questions/sessions",
            json={"character_id": first["character_id"], "session_id": "activity-chat"},
        ).json()
        payload = {
            "action_id": "stable-action-1",
            "expected_revision": 1,
            "action": "draw_question",
            "payload": {},
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
        assert resumed.json()["session"]["phase"] == "answering"

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


def test_conversation_scene_is_session_scoped_and_inherits_character_default(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    first = container.characters.default()
    second = container.characters.clone(first["character_id"])
    container.sessions.ensure_session(
        "scene-chat-a",
        character_id=first["character_id"],
        mode="custom",
    )
    container.sessions.ensure_session(
        "scene-chat-b",
        character_id=first["character_id"],
        mode="custom",
    )
    container.sessions.ensure_session(
        "scene-chat-other",
        character_id=second["character_id"],
        mode="custom",
    )

    with TestClient(app) as client:
        scenes = client.get("/api/v1/scenes")
        assert scenes.status_code == 200
        assert scenes.json()["count"] == 20
        assert all("-preview" not in item["asset_id"] for item in scenes.json()["items"])

        custom = client.post(
            "/api/v1/scenes/custom",
            data={"title": "我的房间", "description": "只属于这段对话的房间。"},
            files={"file": ("room.webp", b"RIFF\x10\x00\x00\x00WEBPscene", "image/webp")},
        )
        assert custom.status_code == 200
        assert custom.json()["custom"] is True
        assert custom.json()["asset_url"].startswith("/api/v1/scene/files/scene-")
        assert client.get(custom.json()["asset_url"]).content == b"RIFF\x10\x00\x00\x00WEBPscene"
        assert client.get("/api/v1/scenes").json()["count"] == 21

        empty = client.get("/api/v1/sessions/scene-chat-a/scene")
        assert empty.status_code == 200
        assert empty.json()["scene"] is None
        assert empty.json()["revision"] == 0

        selected = client.put(
            "/api/v1/sessions/scene-chat-a/scene",
            json={"scene_id": "riverside_evening", "expected_revision": 0},
        )
        assert selected.status_code == 200
        assert selected.json()["scene"]["location"] == "暮色中的河岸边"
        assert selected.json()["revision"] == 1

        stale = client.put(
            "/api/v1/sessions/scene-chat-a/scene",
            json={"scene_id": "rainy_living_room", "expected_revision": 0},
        )
        assert stale.status_code == 409

        inherited = client.get("/api/v1/sessions/scene-chat-b/scene").json()
        assert inherited["scene"]["scene_id"] == "riverside_evening"
        assert inherited["inherited_from_character"] is True
        assert inherited["revision"] == 0

        other = client.get("/api/v1/sessions/scene-chat-other/scene").json()
        assert other["scene"] is None

        custom_selected = client.put(
            "/api/v1/sessions/scene-chat-a/scene",
            json={"scene_id": custom.json()["scene_id"], "expected_revision": 1},
        )
        assert custom_selected.status_code == 200
        assert custom_selected.json()["scene"]["asset_url"] == custom.json()["asset_url"]

        legacy_activity = client.post(
            "/api/v1/activities/scene_companion/sessions",
            json={"character_id": first["character_id"], "session_id": "scene-chat-a"},
        )
        assert legacy_activity.status_code == 422


def test_archived_character_session_scene_returns_controlled_gone_response(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    container.characters.clone(character["character_id"])
    container.sessions.ensure_session(
        "archived-scene-chat",
        character_id=character["character_id"],
        mode="custom",
    )

    with TestClient(app) as client:
        archived = client.post(f"/api/v1/characters/{character['character_id']}/archive")
        assert archived.status_code == 200

        scene = client.get("/api/v1/sessions/archived-scene-chat/scene")

    assert scene.status_code == 410
    assert "archived" in scene.json()["detail"]


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


def test_prompt_inspector_marks_scene_as_ephemeral_system_layer(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    profile_before = deepcopy(container.characters.get(character["character_id"])["card"])
    container.sessions.ensure_session(
        "prompt-scene-session",
        character_id=character["character_id"],
        mode="custom",
    )

    with TestClient(app) as client:
        selected = client.put(
            "/api/v1/sessions/prompt-scene-session/scene",
            json={"scene_id": "riverside_evening", "expected_revision": 0},
        )
        assert selected.status_code == 200
        streamed = client.post(
            "/api/v1/chat/stream",
            headers={"X-Request-ID": "scene-prompt-run"},
            json={
                "message": "风有点大。",
                "session_id": "prompt-scene-session",
                "character_id": character["character_id"],
                "round": 1,
            },
        )
        assert streamed.status_code == 200
        inspection = client.get("/api/v1/runs/scene-prompt-run/prompt-inspection?reveal=true")
        assert inspection.status_code == 200
        layers = inspection.json()["layers"]
        assert any(item["layer"] == "scene_context" for item in layers)
        scene_layer = next(item for item in layers if item["layer"] == "scene_context")
        assert scene_layer["role"] == "system"
        assert scene_layer["content"] == "【当前场景】两个人现在在暮色中的河岸边。"
        assert container.chapters.list_journals(character["character_id"]) == []
        assert container.chapters.list_moments(character["character_id"]) == []
        assert container.characters.get(character["character_id"])["card"] == profile_before


def test_confirmed_continuity_migration_is_idempotent(tmp_path):
    app = make_app(tmp_path)
    container = app.state.container
    character = container.characters.default()
    character_id = character["character_id"]
    marker = "migration:shared-chapters:0.7.0"
    container.database.delete_document(marker)
    container.chapters._migrate_confirmed_continuity()
    first_pass = container.chapters.list_moments(character_id)

    # V2 cards deliberately do not carry the retired continuity tree.
    container.database.delete_document(marker)
    container.chapters._migrate_confirmed_continuity()
    second_pass = container.chapters.list_moments(character_id)

    assert first_pass == []
    assert second_pass == []
    container.conversation.close()
    container.art_catalog.close()
