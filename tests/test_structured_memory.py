from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.memory_registry import MemoryField, MemoryRegistry
from mindspace_graph.memory_service import StructuredMemoryService
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


def _character_registry(max_items: int = 2) -> MemoryRegistry:
    return MemoryRegistry(
        (
            MemoryField(
                field_code="character.memory.preferences",
                target="character_memory",
                path="/preferences",
                display_name="偏好",
                category="偏好",
                value_kind="list",
                reducer="unique_set",
                scope="agent",
                lifecycle="persistent",
                max_items=max_items,
            ),
        )
    )


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


def test_active_limits_are_isolated_by_character_and_legacy_records_remain_readable(tmp_path):
    registry = _character_registry(max_items=2)
    path = tmp_path / "structured-memory.json"
    store = StructuredMemoryStore(path, registry=registry)
    for owner in ("character-a", "character-b"):
        for index in range(3):
            request = _request(index + 1, f"{owner} 偏好 {index}").model_copy(update={"character_id": owner})
            store.record_turn(
                request,
                "已记录。",
                persisted={
                    "user_message_id": f"{owner}-u-{index}",
                    "assistant_message_id": f"{owner}-a-{index}",
                },
                write_receipt=_receipt(
                    {
                        "target": "character_memory",
                        "op": "add",
                        "path": "/preferences/-",
                        "after": f"偏好-{index}",
                    }
                ),
            )

    active = store.snapshot()["active"]
    assert sum(item.get("character_id") == "character-a" for item in active.values()) == 2
    assert sum(item.get("character_id") == "character-b" for item in active.values()) == 2

    legacy = store.snapshot()
    legacy["episodes"]["episode:legacy"] = {"episode_id": "episode:legacy", "text": "旧记录"}
    legacy["active"]["legacy"] = {
        "memory_key": "legacy",
        "field_code": "character.memory.preferences",
        "episode_id": "episode:legacy",
        "updated_at": "2000-01-01T00:00:00+00:00",
        "value": "旧偏好",
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    reloaded = StructuredMemoryStore(path, registry=registry)
    assert any(item["memory_key"] == "legacy" for item in reloaded.list_items(character_id=""))


def test_targeted_rebuild_replaces_only_one_character_and_uses_distinct_episodes(tmp_path):
    registry = _character_registry(max_items=10)

    class Profiles:
        def __init__(self):
            self.characters = SimpleNamespace(
                list=lambda: [{"character_id": "character-a"}, {"character_id": "character-b"}]
            )
            self.memories = {
                "character-a": {"preferences": ["苹果"]},
                "character-b": {"preferences": ["咖啡"]},
            }

        def load_document(self, target, owner):
            if target == "character_memory":
                return deepcopy(self.memories[owner])
            if target == "ai_profile":
                return {"identity": {"name": owner}}
            raise AssertionError(target)

    profiles = Profiles()
    store = StructuredMemoryStore(tmp_path / "memory.json", registry=registry)
    service = StructuredMemoryService(profiles, store, registry=registry)
    service.rebuild()
    before = store.snapshot()
    character_b = {
        key: deepcopy(value)
        for key, value in before["active"].items()
        if value.get("character_id") == "character-b"
    }
    assert len(before["episodes"]) == 2

    profiles.memories["character-a"] = {"preferences": ["草莓"]}
    result = service.rebuild(character_id="character-a")
    after = store.snapshot()

    assert result["bindings"] == 1
    assert {
        key: value for key, value in after["active"].items() if value.get("character_id") == "character-b"
    } == character_b
    assert [
        value["value"] for value in after["active"].values() if value.get("character_id") == "character-a"
    ] == ["草莓"]


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
    preferences = [item for item in second["active"].values() if item["family_key"] == "user:user.preference"]
    assert len(preferences) == 1
    assert preferences[0]["json_tags"][0]["polarity"] == "dislike"


def test_temporal_memory_expires_to_a_tombstone_while_persistent_memory_remains(tmp_path):
    store = StructuredMemoryStore(tmp_path / "structured-memory.json")
    store.record_turn(
        _request(),
        "记住了。",
        persisted=_persisted(1),
        write_receipt=_receipt(
            {
                "target": "runtime_state",
                "op": "replace",
                "path": "/user_state/current_goal",
                "after": "临时目标",
            },
            {
                "target": "user_profile",
                "op": "add",
                "path": "/stable_preferences/likes/-",
                "after": "长期偏好",
            },
        ),
    )
    snapshot = store.snapshot()
    temporal_key = next(key for key, item in snapshot["active"].items() if item["lifecycle"] == "temporal")
    persistent_key = next(key for key, item in snapshot["active"].items() if item["lifecycle"] == "persistent")
    snapshot["active"][temporal_key]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._save(snapshot)

    store.record_turn(
        _request(2, "普通对话"),
        "普通回复",
        persisted=_persisted(2),
        write_receipt=_receipt(),
    )
    pruned = store.snapshot()

    assert temporal_key not in pruned["active"]
    assert persistent_key in pruned["active"]
    assert any(item["memory_key"] == temporal_key and item["reason"] == "expired" for item in pruned["tombstones"])


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
