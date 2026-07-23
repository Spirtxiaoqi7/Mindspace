from __future__ import annotations

from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.models import ChatRequest, JsonWriteReceipt, RetrievedChunk
from mindspace_graph.policies import rank_with_temporal_decay


def _request(round_num: int = 1, message: str = "我喜欢草莓") -> ChatRequest:
    return ChatRequest(
        message=message,
        session_id="memory-session",
        round=round_num,
        character_name="弦月",
        retrieval={
            "temporal_enabled": False,
            "low_exposure_ratio": 0.5,
            "memory_family_limit": 2,
            "starvation_rounds": 2,
        },
    )


def _persisted(index: int) -> dict[str, str]:
    return {"user_message_id": f"user-{index}", "assistant_message_id": f"assistant-{index}"}


def _receipt(*patches: dict) -> JsonWriteReceipt:
    return JsonWriteReceipt(turn_id="round_current", applied=bool(patches), patches=list(patches))


def test_untagged_pool_is_deduplicated_and_strictly_bounded(tmp_path):
    store = StructuredMemoryStore(
        tmp_path / "structured-memory.json",
        max_untagged=3,
        max_untagged_per_session=2,
    )

    for index in range(5):
        request = _request(index + 1, f"普通对话 {index}")
        store.record_turn(
            request,
            f"普通回复 {index}",
            persisted=_persisted(index),
            write_receipt=_receipt(),
        )

    snapshot = store.snapshot()
    assert len(snapshot["untagged"]) == 2
    assert len(snapshot["episodes"]) == 2
    assert snapshot["active"] == {}

    repeated = _request(8, "完全相同")
    store.record_turn(
        repeated,
        "完全相同的回复",
        persisted=_persisted(8),
        write_receipt=_receipt(),
    )
    store.record_turn(
        repeated,
        "完全相同的回复",
        persisted=_persisted(9),
        write_receipt=_receipt(),
    )
    matching = [item for item in store.snapshot()["untagged"] if item["repeat_count"] == 2]
    assert len(matching) == 1


def test_one_committed_json_tag_is_immediately_active_and_keeps_original_text(tmp_path):
    store = StructuredMemoryStore(tmp_path / "structured-memory.json")
    request = _request()
    store.record_turn(
        request,
        "记住啦，你喜欢草莓。",
        persisted=_persisted(1),
        write_receipt=_receipt(
            {
                "target": "user_profile",
                "op": "add",
                "path": "/stable_preferences/likes/-",
                "before": None,
                "after": "草莓",
                "evidence_ids": ["current_user"],
            }
        ),
    )

    snapshot = store.snapshot()
    assert len(snapshot["active"]) == 1
    record = next(iter(snapshot["active"].values()))
    assert record["json_tags"][0] == {
        "tag_id": "json:user_profile:/stable_preferences/likes",
        "field_code": "user.preference.likes",
        "target": "user_profile",
        "path": "/stable_preferences/likes",
        "display_name": "喜欢",
        "category": "偏好",
        "polarity": "like",
    }
    assert "我喜欢草莓" in snapshot["episodes"][record["episode_id"]]["text"]
    assert snapshot["untagged"] == []


def test_opposing_preference_reuses_one_slot_and_multiple_tags_share_one_episode(tmp_path):
    store = StructuredMemoryStore(tmp_path / "structured-memory.json")
    store.record_turn(
        _request(),
        "我记住了。",
        persisted=_persisted(1),
        write_receipt=_receipt(
            {
                "target": "user_profile",
                "op": "add",
                "path": "/stable_preferences/likes/-",
                "after": "草莓",
            },
            {
                "target": "runtime_state",
                "op": "replace",
                "path": "/user_state/current_topic",
                "after": "水果",
            },
        ),
    )
    first = store.snapshot()
    assert len(first["active"]) == 2
    assert len(first["episodes"]) == 1
    assert len({item["episode_id"] for item in first["active"].values()}) == 1
    shared_episode_id = next(iter(first["episodes"]))
    store.set_episode_embedding(shared_episode_id, [0.1, 0.2, 0.3])
    assert store.snapshot()["episodes"][shared_episode_id]["embedding"] == [0.1, 0.2, 0.3]

    store.record_turn(
        _request(2, "其实我不喜欢草莓"),
        "已修正。",
        persisted=_persisted(2),
        write_receipt=_receipt(
            {
                "target": "user_profile",
                "op": "add",
                "path": "/stable_preferences/dislikes/-",
                "after": "草莓",
            }
        ),
    )
    second = store.snapshot()
    preferences = [
        item for item in second["active"].values() if item["family_key"] == "user:user.preference"
    ]
    assert len(preferences) == 1
    assert preferences[0]["json_tags"][0]["polarity"] == "dislike"


def test_fair_ranking_reserves_a_slot_for_an_underexposed_memory():
    high = RetrievedChunk(
        chunk_id="memory:high",
        text="高权重",
        source="memory",
        score=0.99,
        metadata={
            "memory_key": "high",
            "memory_family": "family-high",
            "eligible_misses": 0,
            "last_selected_round": 9,
        },
    )
    medium = RetrievedChunk(
        chunk_id="chat:medium",
        text="普通历史",
        source="chat",
        score=0.9,
    )
    underexposed = RetrievedChunk(
        chunk_id="memory:low",
        text="低曝光但仍相关",
        source="memory",
        score=0.62,
        metadata={
            "memory_key": "low",
            "memory_family": "family-low",
            "eligible_misses": 8,
            "last_selected_round": 1,
        },
    )

    ranked = rank_with_temporal_decay(
        [high, medium, underexposed],
        _request(round_num=10),
        limit=2,
    )

    assert [item.chunk_id for item in ranked] == ["memory:high", "memory:low"]
    assert ranked[1].metadata["starvation_bonus"] > 0


def test_family_limit_delays_repeated_high_weight_slots_until_diverse_fields_are_seen():
    request = _request(round_num=10)
    request.retrieval.low_exposure_ratio = 0
    request.retrieval.memory_family_limit = 1
    chunks = [
        RetrievedChunk(
            chunk_id=f"memory:a{index}",
            text=f"同族 {index}",
            source="memory",
            score=score,
            metadata={"memory_family": "family-a"},
        )
        for index, score in enumerate((0.99, 0.98, 0.97), start=1)
    ]
    chunks.append(
        RetrievedChunk(
            chunk_id="memory:b1",
            text="另一字段族",
            source="memory",
            score=0.7,
            metadata={"memory_family": "family-b"},
        )
    )

    ranked = rank_with_temporal_decay(chunks, request, limit=3)

    assert [item.chunk_id for item in ranked[:2]] == ["memory:a1", "memory:b1"]
