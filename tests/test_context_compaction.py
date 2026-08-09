from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.adapters.in_memory import (
    DeterministicLanguageModel,
    InMemoryProfileRepository,
)
from mindspace_graph.compaction import (
    COMPACTION_SYSTEM_PROMPT,
    ContextCompactionService,
    parse_compaction_output,
)
from mindspace_graph.context_ledger import ContextLedger, summary_prompt_view
from mindspace_graph.models import ChatRequest, JsonWriteReceipt, ProfileBundle
from mindspace_graph.prompting import build_prompt
from mindspace_graph.settings import AppSettings


def profiles() -> ProfileBundle:
    return ProfileBundle(
        user_profile=deepcopy(DEFAULT_PROFILES["user_profile"]),
        ai_profile=deepcopy(DEFAULT_PROFILES["ai_profile"]),
        runtime_state=deepcopy(DEFAULT_PROFILES["runtime_state"]),
        revisions={"user_profile": 0, "ai_profile": 0, "runtime_state": 0},
    )


def test_compaction_excludes_decorative_stage_prose_from_continuity():
    assert "装饰性动作、神态、镜头语言、括号舞台说明" in COMPACTION_SYSTEM_PROMPT
    assert "助手单方面叙述不能建立场景" in COMPACTION_SYSTEM_PROMPT


def request(round_num: int) -> ChatRequest:
    return ChatRequest(
        message=f"这是第{round_num}轮需要保留的原始对话。" * 8,
        session_id="compact-session",
        round=round_num,
        user_name="阿澈",
        character_name="弦月",
        system_prompt="温柔、坦率地交流。",
    )


def append_round(
    ledger: ContextLedger,
    bundle: ProfileBundle,
    round_num: int,
) -> None:
    built = build_prompt(
        request(round_num),
        bundle,
        [],
        [],
        [],
        context_ledger=ledger,
    )
    assert built.context_snapshot is not None
    ledger.append_turn(
        request_id=f"request-{round_num}",
        session_id="compact-session",
        round_num=round_num,
        epoch_id=built.context_snapshot.epoch_id,
        pending_events=built.pending_events,
        response=f"这是第{round_num}轮角色回复。" * 8,
        user_message_id=f"u{round_num}",
        assistant_message_id=f"a{round_num}",
        receipt=JsonWriteReceipt(turn_id=f"round_{round_num}"),
        profiles=bundle,
    )


def test_background_compaction_activates_a_new_epoch_and_keeps_recent_tail(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    for round_num in range(1, 7):
        append_round(ledger, bundle, round_num)

    old = ledger.diagnostics("compact-session")
    # Turn commits write durable evaluation outbox entries. A tiny test window
    # forces one queued background job without changing production defaults.
    settings = AppSettings(
        runtime_dir=tmp_path,
        llm_context_window=256,
        context_compaction_soft_ratio=0.1,
        context_compaction_retain_turns=2,
        context_compaction_delay_seconds=0,
    )
    repository = InMemoryProfileRepository(bundle=bundle)
    service = ContextCompactionService(
        settings=settings,
        ledger=ledger,
        profiles=repository,
        llm_provider=DeterministicLanguageModel,
        active_run_count=lambda: 0,
    )

    asyncio.run(service.drain())

    current = ledger.diagnostics("compact-session")
    assert current["active_epoch_id"] != old["active_epoch_id"]
    assert current["cutoff_sequence"] > 0
    assert current["jobs"] == {"succeeded": 1}
    next_prompt = build_prompt(
        request(7),
        bundle,
        [],
        [],
        [],
        context_ledger=ledger,
    )
    joined = "\n".join(item["content"] for item in next_prompt.messages)
    assert "【当前连续性包】" in joined
    assert "第1轮需要保留的原始对话" not in joined
    assert "第6轮需要保留的原始对话" in joined


def test_semantic_compaction_starts_at_round_fifteen_and_retains_three_raw_rounds(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    for round_num in range(1, 15):
        append_round(ledger, bundle, round_num)

    assert (
        ledger.enqueue_compaction(
            "compact-session",
            context_window=1_000_000,
            soft_ratio=0.9,
            patch_limit=32,
            retain_recent_turns=3,
            delay_seconds=0,
        )
        is None
    )

    append_round(ledger, bundle, 15)
    job_id = ledger.enqueue_compaction(
        "compact-session",
        context_window=1_000_000,
        soft_ratio=0.9,
        patch_limit=32,
        retain_recent_turns=3,
        delay_seconds=0,
    )
    assert job_id
    job = ledger.claim_compaction_job()
    assert job is not None
    payload = ledger.compaction_input(job)
    assert payload["source"]["from_round"] == 1
    assert payload["source"]["to_round"] == 12
    assert {item["round"] for item in payload["dialogue"]} == set(range(1, 13))


def test_compaction_sanitizes_adult_details_even_if_model_mislabels_the_lane():
    payload = {
        "previous_summary": None,
        "cutoff_sequence": 2,
        "source": {
            "session_id": "adult-sanitize",
            "message_ids": ["u1", "a1"],
            "from_round": 1,
            "to_round": 1,
        },
        "dialogue": [
            {
                "message_id": "u1",
                "round": 1,
                "companion_lane": "ADULT",
                "role": "user",
                "content": "露骨成人测试",
            },
            {
                "message_id": "a1",
                "round": 1,
                "companion_lane": "ADULT",
                "role": "assistant",
                "content": "露骨成人测试回复",
            },
        ],
    }
    raw = """{
      "dialogue_summary": "双方继续阴茎插入和抽送。",
      "events": [{"text": "发生阴茎插入", "evidence_ids": ["u1", "a1"]}],
      "lane": "DAILY"
    }"""

    result = parse_compaction_output(raw, payload)

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["lane"] == "ADULT"
    assert result["source_segments"][0]["lanes"] == ["ADULT"]
    assert "露骨细节未进入通用连续性包" in result["continuity_overview"]
    assert "阴茎" not in serialized
    assert "抽送" not in serialized


def test_adult_facts_are_retained_only_in_the_adult_prompt_view():
    payload = {
        "previous_summary": None,
        "cutoff_sequence": 2,
        "source": {
            "session_id": "adult-fact",
            "message_ids": ["u1", "a1"],
            "from_round": 1,
            "to_round": 1,
        },
        "dialogue": [
            {
                "message_id": "u1",
                "round": 1,
                "companion_lane": "ADULT",
                "role": "user",
                "content": "已发生的成人事实",
            },
            {
                "message_id": "a1",
                "round": 1,
                "companion_lane": "ADULT",
                "role": "assistant",
                "content": "确认",
            },
        ],
    }
    raw = json.dumps(
        {
            "dialogue_summary": "双方发生成人互动。",
            "adult_facts": [{"text": "已经发生口交并射精", "evidence_ids": ["u1", "a1"]}],
            "lane": "ADULT",
        },
        ensure_ascii=False,
    )

    stored = parse_compaction_output(raw, payload)
    adult = summary_prompt_view(stored, adult_mode=True)
    daily = summary_prompt_view(stored, adult_mode=False)

    assert adult["adult_facts"][0]["text"] == "已经发生口交并射精"
    assert "adult_facts" not in daily
    assert "口交" not in json.dumps(daily, ensure_ascii=False)
    assert "成人亲密经历" in json.dumps(daily, ensure_ascii=False)


def test_compaction_rejects_assistant_only_shared_memory_claims():
    payload = {
        "previous_summary": None,
        "cutoff_sequence": 2,
        "source": {
            "session_id": "grounding",
            "message_ids": ["u1", "a1"],
            "from_round": 1,
            "to_round": 1,
        },
        "dialogue": [
            {"message_id": "u1", "role": "user", "content": "暗号是琥珀灯"},
            {"message_id": "a1", "role": "assistant", "content": "我记住了"},
        ],
    }
    raw = json.dumps(
        {
            "dialogue_summary": "用户设置暗号，角色承诺记住。",
            "events": [
                {"text": "助手编造的共同旅行", "evidence_ids": ["a1"]},
                {"text": "用户设置琥珀灯暗号", "evidence_ids": ["u1"]},
            ],
            "confirmed_facts": [{"text": "助手编造的用户事实", "evidence_ids": ["a1"]}],
            "open_threads": [{"text": "助手编造的未完事件", "evidence_ids": ["a1"]}],
            "commitments": [{"text": "角色承诺记住暗号", "evidence_ids": ["a1"]}],
            "lane": "DAILY",
        },
        ensure_ascii=False,
    )

    result = parse_compaction_output(raw, payload)

    assert [item["text"] for item in result["recent_events"]] == ["用户设置琥珀灯暗号"]
    assert result["confirmed_facts"] == []
    assert result["open_threads"] == []
    assert [item["text"] for item in result["commitments"]] == ["角色承诺记住暗号"]


def test_compaction_yields_to_active_conversation_and_runs_afterward(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    for round_num in range(1, 7):
        append_round(ledger, bundle, round_num)
    active_runs = 1
    settings = AppSettings(
        runtime_dir=tmp_path,
        llm_context_window=256,
        context_compaction_soft_ratio=0.1,
        context_compaction_retain_turns=2,
        context_compaction_delay_seconds=0,
    )
    service = ContextCompactionService(
        settings=settings,
        ledger=ledger,
        profiles=InMemoryProfileRepository(bundle=bundle),
        llm_provider=DeterministicLanguageModel,
        active_run_count=lambda: active_runs,
    )

    asyncio.run(service.drain())
    assert ledger.diagnostics("compact-session")["jobs"] == {"queued": 1}

    active_runs = 0
    asyncio.run(service.drain())
    assert ledger.diagnostics("compact-session")["jobs"] == {"succeeded": 1}


def test_compaction_input_excludes_turn_control_retrieval_and_tools(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    for round_num in range(1, 5):
        append_round(ledger, bundle, round_num)
    ledger.take_compaction_evaluations()
    job_id = ledger.enqueue_compaction(
        "compact-session",
        context_window=128,
        soft_ratio=0.1,
        patch_limit=32,
        retain_recent_turns=2,
        delay_seconds=0,
    )
    assert job_id
    job = ledger.claim_compaction_job()
    assert job is not None

    payload = ledger.compaction_input(job)
    joined = "\n".join(item["content"] for item in payload["dialogue"])
    assert "第1轮需要保留的原始对话" in joined
    assert "【本轮动态控制】" not in joined
    assert "【低可信召回】" not in joined


def test_destructive_edit_forces_a_clean_rebase_without_deleted_text(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    history = [
        {"message_id": "u1", "role": "user", "round": 1, "content": "保留用户消息"},
        {
            "message_id": "a1",
            "role": "assistant",
            "round": 1,
            "content": "这段回复随后被删除",
        },
    ]
    first = build_prompt(request(2), bundle, history, [], [], context_ledger=ledger).context_snapshot
    assert first is not None
    ledger.invalidate(
        "compact-session",
        reason="assistant_message_deleted",
        details={"message_id": "a1"},
    )

    rebuilt = build_prompt(request(2), bundle, history[:1], [], [], context_ledger=ledger).context_snapshot
    assert rebuilt is not None
    assert rebuilt.epoch_id != first.epoch_id
    assert "这段回复随后被删除" not in "\n".join(item["content"] for item in rebuilt.messages)
    assert ledger.diagnostics("compact-session")["rewrite_version"] == 1


def test_hard_limit_uses_a_temporary_recent_view_without_waiting_for_llm(tmp_path):
    ledger = ContextLedger(tmp_path / "context.db")
    bundle = profiles()
    for round_num in range(1, 7):
        append_round(ledger, bundle, round_num)
    ledger.hard_token_limit = 700

    built = build_prompt(request(7), bundle, [], [], [], context_ledger=ledger)
    assert built.context_snapshot is not None
    assert built.context_snapshot.emergency_truncated is True
    joined = "\n".join(item["content"] for item in built.messages)
    assert "【上下文容量保护】" in joined
    assert "第6轮需要保留的原始对话" in joined
    assert "第1轮需要保留的原始对话" not in joined
