from __future__ import annotations

import json
from datetime import UTC, datetime

from mindspace_graph.adapters.file_storage import JsonProfileRepository, JsonSessionRepository
from mindspace_graph.models import ChatRequest, JsonPatch, JsonUpdatePlan, JsonWriteReceipt


def test_profile_patch_is_atomic_revisioned_and_backed_up(tmp_path):
    repository = JsonProfileRepository(tmp_path / "profiles")
    before = repository.load_bundle()
    plan = JsonUpdatePlan(
        base_revisions=before.revisions,
        trigger="current_user",
        patches=[
            JsonPatch(
                target="user_profile",
                op="replace",
                path="/identity/preferred_name",
                value="小林",
                evidence_ids=["current_user"],
            )
        ],
    )

    receipt = repository.apply_json_update(plan, request=ChatRequest(message="叫我小林"))
    after = repository.load_bundle()

    assert receipt.applied is True
    assert receipt.patches[0]["before"] == "用户"
    assert receipt.patches[0]["after"] == "小林"
    assert after.user_profile["identity"]["preferred_name"] == "小林"
    assert after.revisions["user_profile"] == before.revisions["user_profile"] + 1
    assert list((tmp_path / "profiles" / "history" / "user_profile").glob("*.json"))


def test_regenerate_replaces_round_without_storing_analysis(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    original = ChatRequest(message="第一次提问", session_id="s1", round=1)
    regenerated = ChatRequest(
        message="第一次提问",
        session_id="s1",
        round=1,
        mode="regenerate",
    )
    noop = JsonWriteReceipt(turn_id="round_1")

    sessions.persist_turn(original, "第一次回答", replace_round=False, write_receipt=noop)
    sessions.persist_turn(regenerated, "新的回答", replace_round=True, write_receipt=noop)
    messages = sessions.load_session("s1")["messages"]

    assert len(messages) == 2
    assert messages[1]["content"] == "新的回答"
    assert "analysis" not in messages[1]


def test_turn_persists_distinct_user_and_assistant_atomic_times(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    received = datetime(2026, 7, 21, 14, 5, 6, 123456, tzinfo=UTC)
    request = ChatRequest(
        message="现在几点？",
        session_id="timed",
        server_received_at=received,
        client_sent_at=datetime(2026, 7, 21, 14, 5, 6, 120000, tzinfo=UTC),
    )

    sessions.persist_turn(
        request,
        "我会按照当前时间来回答。",
        replace_round=False,
        write_receipt=JsonWriteReceipt(turn_id="round_1"),
    )
    user, assistant = sessions.load_session("timed")["messages"]

    assert user["timestamp"] == "2026-07-21T14:05:06.123456+00:00"
    assert user["timing"]["server_received_at_utc"] == user["timestamp"]
    assert assistant["timing"]["request_received_at_utc"] == user["timestamp"]
    assert assistant["timestamp"] != user["timestamp"]


def test_initiative_signal_is_internal_and_excluded_from_recall(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    request = ChatRequest(
        message="阿澈不想说什么，但是想让你说点什么。",
        session_id="initiative",
        round=1,
        initiative=True,
        character_name="弦月",
    )
    sessions.persist_turn(
        request,
        "那我陪你听一会儿雨声。",
        replace_round=False,
        write_receipt=JsonWriteReceipt(turn_id="round_1"),
    )

    stored = sessions.load_session("initiative")["messages"]
    recent = sessions.load_recent("initiative")
    chunks = sessions.list_chunks("initiative")

    assert stored[0]["hidden"] is True
    assert stored[1]["kind"] == "initiative_response"
    assert [item["role"] for item in recent] == ["assistant"]
    assert [item["text"] for item in chunks] == ["那我陪你听一会儿雨声。"]
    assert sessions.list_sessions()[0]["message_count"] == 1

    assistant_id = stored[1]["message_id"]
    deleted = sessions.delete_message("initiative", assistant_id)
    assert deleted is not None
    assert deleted.status == "resolved"
    assert sessions.load_session("initiative")["messages"] == []
    assert sessions.load_pending_deletions("initiative") == []


def test_legacy_analysis_is_backed_up_removed_and_filtered(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "session_id": "legacy",
                "messages": [
                    {
                        "message_id": "a1",
                        "role": "assistant",
                        "content": "回复",
                        "analysis": "旧分析",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sessions = JsonSessionRepository(root)

    assert "analysis" not in sessions.load_session("legacy")["messages"][0]
    backups = list((tmp_path / "backups" / "analysis-migration").rglob("legacy.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["messages"][0]["analysis"] == "旧分析"


def test_delete_assistant_reply_keeps_user_and_creates_pending_event(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    request = ChatRequest(message="记住我喜欢茶", session_id="s1", round=1)
    receipt = JsonWriteReceipt(
        turn_id="round_1",
        applied=True,
        patches=[{"target": "user_profile", "path": "/stable_preferences/likes/-"}],
    )
    ids = sessions.persist_turn(request, "我记住了", replace_round=False, write_receipt=receipt)

    event = sessions.delete_message("s1", ids["assistant_message_id"])

    assert event is not None
    assert [item["role"] for item in sessions.load_session("s1")["messages"]] == ["user"]
    pending = sessions.load_pending_deletions("s1")
    assert pending[0].deleted_content == "我记住了"
    assert pending[0].associated_write_receipt["applied"] is True
    sessions.resolve_deletions([pending[0].event_id])
    assert sessions.load_pending_deletions("s1") == []
