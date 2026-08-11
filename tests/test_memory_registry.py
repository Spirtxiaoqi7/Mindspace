from __future__ import annotations

from mindspace_graph.adapters.file_storage import JsonProfileRepository
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.infrastructure.storage.json_patch import read_json_pointer
from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY
from mindspace_graph.memory_service import StructuredMemoryService
from mindspace_graph.models import ChatRequest, JsonPatch, JsonUpdatePlan


def test_registry_has_unique_codes_locations_and_complete_business_metadata():
    registry = DEFAULT_MEMORY_REGISTRY

    assert len(registry.fields) >= 40
    assert len({field.field_code for field in registry.fields}) == len(registry.fields)
    assert len({(field.target, field.path) for field in registry.fields}) == len(registry.fields)
    assert all(field.display_name and field.category and field.max_items > 0 for field in registry.fields)
    assert registry.resolve("character_memory", "/preferences/0").field_code == "character.memory.preferences"
    assert registry.resolve("character_memory", "/preferences/-").reducer == "unique_set"


def test_read_json_pointer_returns_none_when_an_intermediate_node_is_missing():
    document = {"identity": {"preferred_name": "用户"}}

    assert read_json_pointer(document, "/stable_preferences/likes/-") is None
    assert read_json_pointer(document, "/identity/occupation/title") is None
    assert read_json_pointer({"items": []}, "/items/3/name") is None


def test_memory_center_update_delete_and_restore_keep_supported_profile_and_index_aligned(tmp_path):
    profiles = JsonProfileRepository(tmp_path / "profiles")
    store = StructuredMemoryStore(tmp_path / "structured-memory.json")
    service = StructuredMemoryService(profiles, store)
    request = ChatRequest(message="角色说话很温柔", session_id="memory-service", round=1)
    bundle = profiles.load_bundle()
    receipt = profiles.apply_json_update(
        JsonUpdatePlan(
            turn_id="round_1",
            base_revisions=bundle.revisions,
            trigger="current_user",
            patches=[
                JsonPatch(
                    target="ai_profile",
                    op="add",
                    path="/personality/core_traits/-",
                    value="温柔",
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

    updated = service.update(original["memory_key"], "果断")

    assert updated["value"] == "果断"
    assert "果断" in profiles.load_document("ai_profile")["personality"]["core_traits"]
    assert len(service.list_items()) == 1

    assert service.delete(updated["memory_key"]) is True
    assert "果断" not in profiles.load_document("ai_profile")["personality"]["core_traits"]
    assert service.list_items() == []
    assert any(
        item["value"] == "果断" and item["status"] == "invalidated"
        for item in service.list_items(include_history=True)
    )

    restored = service.restore(updated["memory_key"])
    assert restored["value"] == "果断"
    assert "果断" in profiles.load_document("ai_profile")["personality"]["core_traits"]
