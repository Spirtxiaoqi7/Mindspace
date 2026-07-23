from __future__ import annotations

from mindspace_graph.adapters.file_storage import JsonProfileRepository
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY
from mindspace_graph.memory_service import StructuredMemoryService
from mindspace_graph.models import ChatRequest, JsonPatch, JsonUpdatePlan


def test_registry_has_unique_codes_locations_and_complete_business_metadata():
    registry = DEFAULT_MEMORY_REGISTRY

    assert len(registry.fields) >= 40
    assert len({field.field_code for field in registry.fields}) == len(registry.fields)
    assert len({(field.target, field.path) for field in registry.fields}) == len(registry.fields)
    assert all(
        field.display_name and field.category and field.max_items > 0 for field in registry.fields
    )
    assert registry.resolve("user_profile", "/stable_preferences/likes/0").field_code == (
        "user.preference.likes"
    )
    assert registry.resolve("user_profile", "/stable_preferences/likes/-").reducer == (
        "opposing_set"
    )


def test_memory_center_update_delete_and_restore_keep_profile_and_index_aligned(tmp_path):
    profiles = JsonProfileRepository(tmp_path / "profiles")
    store = StructuredMemoryStore(tmp_path / "structured-memory.json")
    service = StructuredMemoryService(profiles, store)
    request = ChatRequest(message="我喜欢草莓", session_id="memory-service", round=1)
    bundle = profiles.load_bundle()
    receipt = profiles.apply_json_update(
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
    store.record_turn(
        request,
        "我记住了。",
        persisted={"user_message_id": "u1", "assistant_message_id": "a1"},
        write_receipt=receipt,
    )
    original = service.list_items()[0]

    updated = service.update(original["memory_key"], "蓝莓")

    assert updated["value"] == "蓝莓"
    assert profiles.load_document("user_profile")["stable_preferences"]["likes"] == ["蓝莓"]
    assert len(service.list_items()) == 1

    assert service.delete(updated["memory_key"]) is True
    assert profiles.load_document("user_profile")["stable_preferences"]["likes"] == []
    assert service.list_items() == []
    assert any(
        item["value"] == "蓝莓" and item["status"] == "invalidated"
        for item in service.list_items(include_history=True)
    )

    restored = service.restore(updated["memory_key"])
    assert restored["value"] == "蓝莓"
    assert profiles.load_document("user_profile")["stable_preferences"]["likes"] == ["蓝莓"]
