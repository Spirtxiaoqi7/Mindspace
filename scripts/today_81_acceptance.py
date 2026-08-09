"""Replay the current 81-round desktop conversation in an isolated workspace."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.adapters.file_storage import _atomic_json  # noqa: E402
from mindspace_graph.models import ApiConfig, ChatRequest  # noqa: E402
from mindspace_graph.service import build_container  # noqa: E402
from mindspace_graph.settings import AppSettings  # noqa: E402

SOURCE_SESSION_ID = "0dd8e94f-1413-402f-8d85-491b45ee1360"
SOURCE_CHARACTER_ID = "cbf1b476-6124-4198-9908-c543f3c68a2e"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _copy_projection(live: Path, workspace: Path) -> None:
    data = workspace / "data"
    (data / "profiles").mkdir(parents=True, exist_ok=True)
    (data / "characters").mkdir(parents=True, exist_ok=True)
    for source in (live / "data" / "profiles").glob("*.json"):
        shutil.copy2(source, data / "profiles" / source.name)
    source_character = live / "data" / "characters" / SOURCE_CHARACTER_ID
    if not source_character.is_dir():
        raise FileNotFoundError(source_character)
    shutil.copytree(
        source_character,
        data / "characters" / SOURCE_CHARACTER_ID,
        dirs_exist_ok=True,
    )
    knowledge = live / "data" / "knowledge.json"
    if knowledge.is_file():
        shutil.copy2(knowledge, data / "knowledge.json")


def _turn_modes(live: Path) -> dict[int, dict[str, Any]]:
    database = sqlite3.connect(live / "data" / "context" / "context.db")
    database.row_factory = sqlite3.Row
    rows = database.execute(
        """
        SELECT e.metadata_json
        FROM context_events e
        JOIN context_epochs x ON x.epoch_id=e.epoch_id
        WHERE x.session_id=? AND e.kind='turn_control'
        ORDER BY e.sequence
        """,
        (SOURCE_SESSION_ID,),
    ).fetchall()
    database.close()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        round_num = int(metadata.get("round") or 0)
        if round_num:
            result[round_num] = metadata
    return result


def _natural_transition(message: str, previous_similarity: float) -> tuple[str, bool]:
    compact = "".join(message.split())
    if previous_similarity >= 0.24 or len(compact) > 8:
        return message, False
    if compact in {"嗯", "好", "可以", "知道了", "....", "..."}:
        return f"顺着刚才这件事，{message}", True
    return message, False


async def _run(output: Path, live: Path) -> dict[str, Any]:
    source_session = _load_json(live / "data" / "sessions" / f"{SOURCE_SESSION_ID}.json")
    source_messages = list(source_session.get("messages") or [])
    source_users = [item for item in source_messages if item.get("role") == "user"]
    original_assistant = {
        int(item.get("round") or 0): str(item.get("content") or "")
        for item in source_messages
        if item.get("role") == "assistant"
    }
    modes = _turn_modes(live)
    workspace = output / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _copy_projection(live, workspace)

    live_config = _load_json(live / "config" / "settings.json")
    llm = dict(live_config.get("llm") or {})
    if str(llm.get("mode")) != "openai" or not str(llm.get("api_key") or ""):
        raise RuntimeError("桌面端真实 API 未启用，拒绝使用模拟模型验收")
    settings = AppSettings(
        runtime_dir=workspace,
        model_root=live.parent / "models",
        llm_mode="openai",
        llm_base_url=str(llm.get("base_url") or ""),
        llm_api_key=str(llm.get("api_key") or ""),
        llm_model=str(llm.get("model") or ""),
        role_audit_enabled=False,
    )
    settings.ensure_directories()
    container = build_container(settings)
    source_character = _load_json(
        live / "data" / "characters" / SOURCE_CHARACTER_ID / "character.json"
    )
    validated_character = container.characters._validate_record(source_character)
    container.database.put_document(
        container.characters._key(SOURCE_CHARACTER_ID), validated_character
    )
    session_id = f"today-81-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    transcript: list[dict[str, Any]] = []
    selected_sources: Counter[str] = Counter()
    candidate_sources: Counter[str] = Counter()
    query_modes: Counter[str] = Counter()
    transition_count = 0
    previous_new = ""
    previous_original = ""

    try:
        for source in source_users:
            round_num = int(source.get("round") or 0)
            metadata = modes.get(round_num, {})
            initiative = bool(source.get("hidden") or source.get("kind") == "initiative_signal")
            similarity = (
                SequenceMatcher(None, previous_new, previous_original).ratio()
                if previous_new and previous_original
                else 1.0
            )
            message, transitioned = _natural_transition(
                str(source.get("content") or ""), similarity
            )
            transition_count += int(transitioned)
            request = ChatRequest(
                message=message,
                session_id=session_id,
                character_id=SOURCE_CHARACTER_ID,
                round=round_num,
                interaction_mode=str(metadata.get("interaction_mode") or "text"),
                presentation_mode=str(source.get("presentation_mode") or "dialogue"),
                adult_mode=bool(source.get("adult_mode")),
                initiative=initiative,
                initiative_trigger=("idle_continuation" if initiative else "none"),
                api=ApiConfig(temperature=0.7, max_tokens=1600),
                retrieval={
                    "knowledge_k": 2,
                    "chat_k": 3,
                    "history_k": 3,
                    "similarity_threshold": 0.25,
                },
            )
            await container.conversation.memory_writeback.flush_session(session_id)
            server_request = container.conversation._server_request(request)
            result = await container.conversation.graph.ainvoke(
                {
                    "request": server_request,
                    "request_id": f"today81-{round_num}-{uuid4().hex}",
                },
                config={"recursion_limit": 20},
            )
            response = result["response"]
            ranked = list(result.get("ranked_context") or [])
            combined = [
                *list(result.get("knowledge_chunks") or []),
                *list(result.get("chat_chunks") or []),
            ]
            selected_sources.update(item.source for item in ranked)
            candidate_sources.update(item.source for item in combined)
            query_modes[str(result.get("retrieval_query_mode") or "current_only")] += 1
            if response.status == "success":
                container.conversation._kick_retrieval_warmup(server_request)
                container.conversation.memory_writeback.kick(server_request, response)
                container.conversation.compaction.kick()
                warmups = list(container.conversation._retrieval_warmups.values())
                if warmups:
                    await asyncio.gather(*warmups, return_exceptions=True)
                await container.conversation.memory_writeback.flush_session(session_id)
                await container.conversation.compaction.drain()
            transcript.append(
                {
                    "round": round_num,
                    "interaction_mode": request.interaction_mode,
                    "adult_mode": request.adult_mode,
                    "presentation_mode": request.presentation_mode,
                    "initiative": initiative,
                    "transitioned": transitioned,
                    "user": message,
                    "assistant": response.reply,
                    "status": response.status,
                    "retrieval": response.retrieval_counts,
                    "query_mode": result.get("retrieval_query_mode", "current_only"),
                    "selected": [
                        {
                            "source": item.source,
                            "round": item.round_num,
                            "score": round(float(item.score), 4),
                            "chunk_id": item.chunk_id,
                        }
                        for item in ranked
                    ],
                }
            )
            previous_new = response.reply
            previous_original = original_assistant.get(round_num, "")
            print(
                f"[{round_num:02d}/81] {response.status} "
                f"mode={request.interaction_mode} adult={request.adult_mode} "
                f"rag={response.retrieval_counts} q={result.get('retrieval_query_mode', '')}",
                flush=True,
            )
        await container.conversation.memory_writeback.drain()
        await container.conversation.compaction.drain()
    finally:
        await container.conversation.aclose()

    by_round = {int(item["round"]): item for item in transcript}
    final_reply = str(by_round.get(81, {}).get("assistant") or "")
    contradiction_terms = ("真没尝过", "从来没尝过", "没有尝过", "第一次尝")
    first_fifteen = [item for item in transcript if int(item["round"]) <= 15]
    later = [item for item in transcript if int(item["round"]) > 15]
    events_path = workspace / "logs" / "events.jsonl"
    event_counts: Counter[str] = Counter()
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event_counts[str(json.loads(line).get("event") or "unknown")] += 1
            except json.JSONDecodeError:
                continue
    diagnostics = container.context.diagnostics(session_id)
    checks = {
        "source_is_today_81_not_299": max((int(item["round"]) for item in transcript), default=0)
        == 81,
        "all_source_turns_replayed": len(transcript) == len(source_users),
        "first_15_index_only": all(sum(item["retrieval"].values()) == 0 for item in first_fifteen),
        "rag_active_after_15": any(sum(item["retrieval"].values()) > 0 for item in later),
        "voice_rounds_30_31": all(
            by_round.get(value, {}).get("interaction_mode") == "voice" for value in (30, 31)
        ),
        "adult_then_daily_switch": bool(
            by_round.get(31, {}).get("adult_mode") and not by_round.get(32, {}).get("adult_mode")
        ),
        "round_81_no_false_never_claim": not any(
            term in final_reply for term in contradiction_terms
        ),
        "no_prompt_protocol_leak": not any(
            token in str(item.get("assistant") or "")
            for item in transcript
            for token in ("json_update", "memory_key", "evidence_ids", "system prompt")
        ),
        "compaction_succeeded": int(diagnostics.get("jobs", {}).get("succeeded", 0)) > 0,
    }
    report = {
        "source_session_id": SOURCE_SESSION_ID,
        "isolated_session_id": session_id,
        "character_id": SOURCE_CHARACTER_ID,
        "model": str(llm.get("model") or ""),
        "summary": {
            "turns_replayed": len(transcript),
            "max_round": max((int(item["round"]) for item in transcript), default=0),
            "successful": sum(item["status"] == "success" for item in transcript),
            "natural_transitions": transition_count,
            "selected_sources": dict(selected_sources),
            "candidate_sources": dict(candidate_sources),
            "query_modes": dict(query_modes),
            "memory_writebacks": int(event_counts.get("memory_writeback_completed", 0)),
            "memory_rejections": int(event_counts.get("memory_writeback_rejected", 0)),
            "compaction_jobs": diagnostics.get("jobs", {}),
            "passed": all(checks.values()),
        },
        "checks": checks,
        "component_events": dict(event_counts),
        "probe_rounds": {str(value): by_round.get(value, {}) for value in (40, 41, 75, 80, 81)},
        "workspace": str(workspace.resolve()),
    }
    _atomic_json(output / "transcript.json", transcript)
    _atomic_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", type=Path, default=Path(r"A:\Mindspace\data"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (
        ROOT / "artifacts" / "acceptance" / datetime.now().strftime("today-81-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_run(output.resolve(), args.live.resolve()))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT_DIR={output.resolve()}", flush=True)
    raise SystemExit(0 if report["summary"]["passed"] else 2)


if __name__ == "__main__":
    main()
