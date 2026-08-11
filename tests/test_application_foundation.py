import json
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
    JsonWriteReceipt,
    ProfileBundle,
    RoleAuditResult,
)
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.prompting import build_prompt
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
            edited["custom_profile"] = "事务内写入的补充资料"
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


def test_regenerate_withdraws_old_memory_context_and_indexes_atomically(tmp_path):
    database = ProductDatabase(tmp_path / "data" / "context.db")
    profiles = JsonProfileRepository(tmp_path / "profiles", database=database)
    sessions = JsonSessionRepository(tmp_path / "sessions", database=database)
    memory = StructuredMemoryStore(tmp_path / "memory.json", database=database)
    ledger = ContextLedger(tmp_path / "data" / "context.db", database=database)
    bundle = profiles.load_bundle()
    original = ChatRequest(message="我喜欢草莓", session_id="regen", round=1)
    original_built = build_prompt(
        original,
        bundle,
        [],
        [],
        [],
        context_ledger=ledger,
    )
    assert original_built.context_snapshot is not None
    receipt = JsonWriteReceipt(
        turn_id="round_1",
        applied=True,
        patches=[
            {
                "target": "runtime_state",
                "op": "replace",
                "path": "/user_state/current_topic",
                "before": "",
                "after": "草莓",
                "evidence_ids": ["current_user"],
            }
        ],
    )
    with database.transaction(operation="seed_regenerate_test"):
        persisted = sessions.persist_turn(
            original,
            "旧回答会被撤销",
            replace_round=False,
            write_receipt=receipt,
        )
        memory.record_turn(
            original,
            "旧回答会被撤销",
            persisted=persisted,
            write_receipt=receipt,
        )
        ledger.append_turn(
            request_id="old-request",
            session_id="regen",
            round_num=1,
            epoch_id=original_built.context_snapshot.epoch_id,
            pending_events=original_built.pending_events,
            response="旧回答会被撤销",
            user_message_id=persisted["user_message_id"],
            assistant_message_id=persisted["assistant_message_id"],
            receipt=receipt,
            profiles=bundle,
        )
        ledger.record_model_usage(
            request_id="old-request",
            session_id="regen",
            round_num=1,
            usages=[{"request_kind": "generation", "model": "test", "total_tokens": 10}],
        )
        ledger.enqueue_role_audit(session_id="regen", round_num=1, payload={"response": "旧回答"})

    regenerated = original.model_copy(update={"mode": "regenerate"})
    regenerated_built = build_prompt(
        regenerated,
        bundle,
        [],
        [],
        sessions.load_recent("regen"),
        context_ledger=ledger,
    )
    assert regenerated_built.context_snapshot is not None

    with pytest.raises(RuntimeError):
        with database.transaction(operation="regenerate_rollback_probe"):
            memory.forget_session("regen", 1)
            ledger.replace_round("regen", 1)
            sessions.persist_turn(
                regenerated,
                "不会提交的新回答",
                replace_round=True,
                write_receipt=JsonWriteReceipt(turn_id="round_1"),
            )
            raise RuntimeError("force rollback")

    assert sessions.load_session("regen")["messages"][-1]["content"] == "旧回答会被撤销"
    assert ledger.find_turn_commit("old-request") is not None
    assert any("旧回答会被撤销" in episode["text"] for episode in memory.snapshot()["episodes"].values())

    with database.transaction(operation="regenerate_commit"):
        memory.forget_session("regen", 1)
        replaced = ledger.replace_round("regen", 1)
        new_ids = sessions.persist_turn(
            regenerated,
            "新的回答",
            replace_round=True,
            write_receipt=JsonWriteReceipt(turn_id="round_1"),
        )
        ledger.append_turn(
            request_id="new-request",
            session_id="regen",
            round_num=1,
            epoch_id=regenerated_built.context_snapshot.epoch_id,
            pending_events=regenerated_built.pending_events,
            response="新的回答",
            user_message_id=new_ids["user_message_id"],
            assistant_message_id=new_ids["assistant_message_id"],
            receipt=JsonWriteReceipt(turn_id="round_1"),
            profiles=bundle,
        )

    assert replaced["commits"] == 1
    assert sessions.load_session("regen")["messages"][-1]["content"] == "新的回答"
    assert ledger.find_turn_commit("old-request") is None
    assert ledger.find_turn_commit("new-request") is not None
    snapshot = memory.snapshot()
    assert all("旧回答会被撤销" not in episode.get("text", "") for episode in snapshot["episodes"].values())
    assert all(
        "旧回答会被撤销" not in item.get("source_episode", {}).get("text", "")
        for item in snapshot["tombstones"]
    )
    with ledger._connect() as db:
        context_text = "\n".join(
            str(row["content"])
            for row in db.execute("SELECT content FROM context_events WHERE session_id='regen'").fetchall()
        )
        assert "旧回答会被撤销" not in context_text
        assert db.execute("SELECT COUNT(*) FROM model_usage WHERE session_id='regen'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM role_audit_jobs WHERE session_id='regen'").fetchone()[0] == 0


def test_alias_identity_resolves_to_the_same_entity_without_duplication(tmp_path):
    database = ProductDatabase(tmp_path / "context.db")
    entities = EntityRegistry(database)
    strawberry = entities.resolve("草莓", scope="user", entity_type="user.preference")
    entities.add_alias(str(strawberry), "士多啤梨")
    assert entities.resolve("士多啤梨", scope="user", entity_type="user.preference", create=False) == strawberry


def test_profile_schema_migrates_legacy_user_fields_to_compact_v13_document(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    legacy = {
        "schema_version": "1.2.0",
        "profile_type": "user",
        "revision": 7,
        "identity": {
            "preferred_name": "柒君",
            "gender": "男",
            "occupation": "已废弃字段",
        },
        "stable_preferences": {"likes": ["草莓"], "dislikes": []},
        "communication_preferences": {"response_length": "固定两百字"},
    }
    (root / "user-profile.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    migrated = JsonProfileRepository(root).load_document("user_profile")

    assert migrated == {
        "schema_version": "1.3.0",
        "profile_type": "user",
        "revision": 7,
        "identity": {"preferred_name": "柒君", "gender": "男"},
        "custom_profile": "",
    }


def test_profile_gender_defaults_and_validation_are_explicit(tmp_path):
    profiles = JsonProfileRepository(tmp_path / "profiles")
    user = profiles.load_document("user_profile")
    assistant = profiles.load_document("ai_profile")

    assert user["identity"]["gender"] == "男"
    assert assistant["identity"]["gender"] == "女"
    assert user["schema_version"] == "1.3.0"
    assert user["custom_profile"] == ""

    invalid = deepcopy(user)
    invalid["identity"]["gender"] = "未设置"
    with pytest.raises(ValueError, match="must be 男、女或不指定"):
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


def test_role_audit_writes_one_bounded_continuity_digest_for_next_turn(tmp_path):
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
            recent_event_summary="用户提出继续文字交流，助手作出回应。",
            event_progression="话题从问候推进到交流方式。",
            open_threads=["继续当前话题"],
        ),
    )
    snapshot = ledger.prepare_context(
        session_id="role",
        static_messages=[{"role": "system", "content": "角色契约"}],
        profiles=profiles,
        history=[],
    )
    digest = [item["content"] for item in snapshot.messages if "近期连续性摘要" in item["content"]]
    assert len(digest) == 1
    assert "保持纯文字交流" in digest[0]
    assert "话题从问候推进到交流方式" in digest[0]
    diagnostics = ledger.diagnostics("role")
    assert diagnostics["model_visible_event_count"] >= 1
