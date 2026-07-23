from copy import deepcopy

import pytest

from mindspace_graph.adapters.file_storage import (
    DEFAULT_PROFILES,
    JsonProfileRepository,
    JsonSessionRepository,
)
from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore
from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.entity_registry import EntityRegistry
from mindspace_graph.models import (
    ApiConfig,
    ChatRequest,
    JsonPatch,
    JsonUpdatePlan,
    JsonWriteReceipt,
    ProfileBundle,
    RoleAuditResult,
)
from mindspace_graph.policies import normalize_json_update
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.retrieval_fusion import BM25Plus, reciprocal_rank_fusion


def test_shared_transaction_rolls_back_every_canonical_store(tmp_path):
    database = ProductDatabase(tmp_path / "data" / "context.db")
    profiles = JsonProfileRepository(tmp_path / "profiles", database=database)
    sessions = JsonSessionRepository(tmp_path / "sessions", database=database)
    memory = StructuredMemoryStore(tmp_path / "memory.json", database=database)
    original = profiles.load_document("user_profile")
    request = ChatRequest(message="我喜欢草莓", session_id="tx", round=1)

    with pytest.raises(RuntimeError):
        with database.transaction(operation="fault_injection"):
            edited = deepcopy(original)
            edited["identity"]["occupation"] = "测试员"
            profiles.save_document("user_profile", edited)
            persisted = sessions.persist_turn(
                request,
                "记住了",
                replace_round=False,
                write_receipt=JsonWriteReceipt(turn_id="round_1"),
            )
            memory.record_turn(
                request,
                "记住了",
                persisted=persisted,
                write_receipt=JsonWriteReceipt(turn_id="round_1"),
            )
            raise RuntimeError("simulated process failure")

    assert profiles.load_document("user_profile") == original
    assert sessions.load_session("tx")["messages"] == []
    assert memory.snapshot()["active"] == {}
    assert database.integrity_check()["ok"] is True


def test_alias_identity_removes_opposing_value_without_model_judgment(tmp_path):
    database = ProductDatabase(tmp_path / "context.db")
    entities = EntityRegistry(database)
    strawberry = entities.resolve("草莓", scope="user", entity_type="user.preference")
    entities.add_alias(str(strawberry), "士多啤梨")
    assert (
        entities.resolve("士多啤梨", scope="user", entity_type="user.preference", create=False)
        == strawberry
    )

    user = deepcopy(DEFAULT_PROFILES["user_profile"])
    user["stable_preferences"]["likes"] = ["草莓"]
    profiles = ProfileBundle(
        user_profile=user,
        ai_profile=deepcopy(DEFAULT_PROFILES["ai_profile"]),
        runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
    )
    plan = JsonUpdatePlan(
        trigger="current_user",
        patches=[
            JsonPatch(
                target="user_profile",
                op="add",
                path="/stable_preferences/dislikes",
                value="士多啤梨",
                evidence_ids=["current_user"],
            )
        ],
    )
    normalized = normalize_json_update(plan, profiles, entities)
    assert [(item.op, item.path) for item in normalized.patches] == [
        ("remove", "/stable_preferences/likes/0"),
        ("add", "/stable_preferences/dislikes/-"),
    ]


def test_profile_schema_rejects_incomplete_advanced_document(tmp_path):
    profiles = JsonProfileRepository(tmp_path / "profiles")
    invalid = profiles.load_document("user_profile")
    invalid.pop("stable_preferences")
    with pytest.raises(ValueError, match="stable_preferences"):
        profiles.save_document("user_profile", invalid)


def test_bm25_plus_and_rrf_keep_independent_rank_evidence():
    scorer = BM25Plus(["苹果 香蕉", "苹果 苹果 梨", "天气 晴朗"])
    scores = scorer.scores("苹果")
    assert scores[1] > scores[0] > scores[2]
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], rrf_k=60)
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["c"]


def test_openai_usage_extracts_standard_cached_tokens():
    model = OpenAICompatibleLanguageModel()
    model._capture_usage(  # noqa: SLF001 - parser contract test
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 80},
            }
        },
        ApiConfig(model="test"),
        "generation",
    )
    usage = model.take_usage()
    assert usage is not None
    assert usage.cached_tokens == 80
    assert usage.cache_source == "prompt_tokens_details.cached_tokens"


def test_role_audit_only_appends_next_turn_correction(tmp_path):
    database = ProductDatabase(tmp_path / "context.db")
    ledger = ContextLedger(tmp_path / "context.db", database=database)
    profiles = ProfileBundle(
        user_profile=deepcopy(DEFAULT_PROFILES["user_profile"]),
        ai_profile=deepcopy(DEFAULT_PROFILES["ai_profile"]),
        runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
        revisions={"user_profile": 0, "ai_profile": 0, "runtime_state": 0},
    )
    ledger.prepare_context(
        session_id="role",
        static_messages=[{"role": "system", "content": "角色契约"}],
        profiles=profiles,
        history=[],
    )
    ledger.enqueue_role_audit(session_id="role", round_num=1, payload={"response": "x"})
    job = ledger.claim_role_audit()
    assert job is not None
    ledger.complete_role_audit(
        job,
        RoleAuditResult(
            is_consistent=False,
            severity="reality",
            confidence=0.95,
            evidence=["声称实体接触"],
            next_turn_instruction="保持纯文字交流，不声称发生实体接触。",
        ),
    )
    snapshot = ledger.prepare_context(
        session_id="role",
        static_messages=[{"role": "system", "content": "角色契约"}],
        profiles=profiles,
        history=[],
    )
    assert any("保持纯文字交流" in item["content"] for item in snapshot.messages)
