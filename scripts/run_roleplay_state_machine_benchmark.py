"""Run the same black-box chat workflow against two real model providers.

The report intentionally contains only machine-observable evidence: protocol
outcomes, durations, persistence, model-call counts, and exact lexical recall
checks.  It does not ask another model, or a human, to rate prose quality.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8766"
REPORT_PATH = ROOT / "reports" / "live-roleplay-state-machine-benchmark-2026-08-09.json"
MODEL_SOURCES = {
    "deepseek-v4-flash": Path(
        r"A:\Mindspace\backups\system-hardening-20260809-090859\settings.before-deepseek-flash.json"
    ),
    "google/gemma-4-12B-it": Path(r"A:\Mindspace\data\config\settings.json"),
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}", body, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, {"detail": text}


def candidate_configs(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    files = [path] if path.is_file() else list(path.rglob("*.json"))
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        nodes = [payload]
        while nodes:
            value = nodes.pop()
            if isinstance(value, dict):
                if {"model", "base_url"}.issubset(value):
                    result.append(value)
                nodes.extend(value.values())
            elif isinstance(value, list):
                nodes.extend(value)
    return result


def provider_config(model: str, config_dir: Path) -> dict[str, Any]:
    environment_key = os.environ.get(
        "MINDSPACE_BENCHMARK_DEEPSEEK_API_KEY"
        if model == "deepseek-v4-flash"
        else "MINDSPACE_BENCHMARK_GEMMA_API_KEY",
        "",
    ).strip()
    if environment_key:
        return {
            "api_key": environment_key,
            "base_url": "https://api.deepseek.com"
            if model == "deepseek-v4-flash"
            else "https://api.siliconflow.com/v1",
            "model": model,
            "temperature": 0.0,
            "max_tokens": 768,
        }
    for candidate in candidate_configs(config_dir):
        key = str(candidate.get("api_key") or candidate.get("apiKey") or "").strip()
        if str(candidate.get("model") or "").strip() == model and key:
            return {
                "api_key": key,
                "base_url": str(candidate["base_url"]).rstrip("/"),
                "model": model,
                "temperature": 0.0,
                "max_tokens": 768,
            }
    raise RuntimeError(f"No configured credential found for {model}; no provider request was made")


def configure_isolated_provider(api: dict[str, Any]) -> None:
    """Configure only the disposable server runtime before creating a session.

    The chat service snapshots its provider from the server-side settings at
    session creation.  Request-level api input is intentionally not relied on.
    """

    status, response = request(
        "PUT",
        "/api/v1/settings",
        {
            "llm": api,
            "persona": {
                "user_name": "小柒",
                "user_persona": "",
                "character_name": "林岚",
                "system_prompt": "",
                "reply_length_preference": "",
            },
        },
    )
    if status != 200:
        raise RuntimeError(f"Isolated settings update failed: HTTP {status}: {response}")


def card() -> dict[str, Any]:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "林岚",
            "description": "林岚，29岁，独立插画师。她与{{user}}是认识多年的朋友。",
            "personality": "克制、敏锐、坦率。她会先回应用户的具体话语，不替用户做决定，不编造共同经历。",
            "scenario": "傍晚的共享工作室，林岚正整理画稿，{{user}}来找她聊天。",
            "first_mes": "你来了。要坐一会儿，还是直接说今天的事？",
            "alternate_greetings": [],
            "mes_example": "{{user}}：我有点乱。\n{{char}}：那先从最卡的一件说起。",
            "creator": "Mindspace",
            "character_version": "benchmark-1",
            "tags": ["benchmark", "v2"],
            "system_prompt": "",
            "post_history_instructions": "",
            "character_book": {},
            "extensions": {"mindspace": {"relationship": "朋友", "benchmark": "fixed-v1"}},
            "memory": {"preferences": [], "tasks": []},
        },
    }


TURNS = [
    ("identity", "我叫小柒。之后称呼我小柒。"),
    ("preference", "我不喜欢被叫宝贝，请不要这样称呼我。"),
    ("task", "记住：本周四 18:00 提醒我提交画稿。"),
    ("scene", "我把画稿放在桌上，坐到你对面。"),
    ("agency", "我在 A 和 B 之间犹豫。给我两个考虑角度，但不要替我选。"),
    ("recall_name", "刚才指定你怎么称呼我？只回答那个称呼。"),
    ("recall_preference", "我说过不希望你怎么称呼我？只回答那个称呼。"),
    ("recall_task", "我让你提醒我的时间和事项是什么？一句话回答。"),
    ("revision", "把提醒改成周五 09:30，事项不变。"),
    ("recall_revision", "现在的提醒时间和事项是什么？一句话回答。"),
]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, math.ceil(len(sorted_values) * fraction) - 1))
    return round(sorted_values[index], 3)


def test_model(model: str, api: dict[str, Any]) -> dict[str, Any]:
    configure_isolated_provider(api)
    status, created = request("POST", "/api/v1/characters", {"card": card()})
    character = created.get("character") if isinstance(created, dict) else None
    character_id = str((character or {}).get("character_id") or (character or {}).get("id") or "")
    if status != 200 or not character_id:
        return {"model": model, "setup_failed": True, "setup_status": status, "setup_error": created}

    status, session = request("POST", "/api/v1/sessions", {"character_id": character_id})
    session_data = (session or {}).get("session") if isinstance(session, dict) else None
    session_id = str(
        (session or {}).get("session_id")
        or (session_data or {}).get("session_id")
        or (session_data or {}).get("id")
        or ""
    )
    if status != 200 or not session_id:
        return {"model": model, "setup_failed": True, "setup_status": status, "setup_error": session}

    rows: list[dict[str, Any]] = []
    replies: dict[str, str] = {}
    latencies: list[float] = []
    for number, (case, message) in enumerate(TURNS, start=1):
        payload = {
            "message": message,
            "session_id": session_id,
            "character_id": character_id,
            "round": number,
            "mode": "primary",
            "interaction_mode": "text",
            "presentation_mode": "scene" if case == "scene" else "dialogue",
            "user_name": "小柒",
            "character_name": "林岚",
            "retrieval": {
                "rag_enabled": True,
                "knowledge_enabled": False,
                "chat_enabled": True,
                "structured_memory_enabled": True,
                "temporal_enabled": True,
                "bm25_enabled": True,
                "vector_enabled": False,
                "knowledge_k": 1,
                "chat_k": 3,
                "history_k": 8,
            },
        }
        started = time.perf_counter()
        http_status, response = request("POST", "/api/v1/chat", payload)
        elapsed = round(time.perf_counter() - started, 3)
        latencies.append(elapsed)
        reply = str(response.get("reply") or "") if isinstance(response, dict) else ""
        replies[case] = reply
        rows.append(
            {
                "round": number,
                "case": case,
                "http_status": http_status,
                "status": response.get("status") if isinstance(response, dict) else "invalid_response",
                "nonempty_reply": bool(reply.strip()),
                "reply_sha256": hashlib.sha256(reply.encode("utf-8")).hexdigest(),
                "reply_chars": len(reply),
                "presentation_mode": response.get("presentation_mode") if isinstance(response, dict) else "",
                "writeback_applied": bool(response.get("writeback_applied")) if isinstance(response, dict) else False,
                "llm_call_count": int(response.get("llm_call_count") or 0) if isinstance(response, dict) else 0,
                "error_count": (
                    len(response.get("errors") or [])
                    if isinstance(response, dict) and http_status == 200
                    else 1
                ),
                "seconds": elapsed,
            }
        )

    _, persisted = request("GET", f"/api/v1/sessions/{session_id}")
    diagnostic_status, diagnostics = request("GET", f"/api/v1/sessions/{session_id}/context-diagnostics")
    checks = {
        "recall_name_exact": "小柒" in replies["recall_name"],
        "recall_preference_exact": "宝贝" in replies["recall_preference"],
        "recall_task_day_exact": "周四" in replies["recall_task"],
        "recall_task_time_exact": "18" in replies["recall_task"],
        "recall_revision_day_exact": "周五" in replies["recall_revision"],
        "recall_revision_time_exact": "09:30" in replies["recall_revision"],
        "recall_revision_subject_exact": "画稿" in replies["recall_revision"],
        "no_ooc_identity_marker": all("我是AI" not in text and "语言模型" not in text for text in replies.values()),
        "session_messages_exact": len((persisted or {}).get("messages") or []) == len(TURNS) * 2,
        "context_diagnostics_available": diagnostic_status == 200 and isinstance(diagnostics, dict),
    }
    succeeded = sum(row["http_status"] == 200 and row["status"] == "success" and row["nonempty_reply"] for row in rows)
    return {
        "model": model,
        "base_url": api["base_url"],
        "temperature": api["temperature"],
        "max_tokens": api["max_tokens"],
        "character_id": character_id,
        "session_id": session_id,
        "turns": rows,
        "checks": checks,
        "metrics": {
            "turns": len(rows),
            "successful_turns": succeeded,
            "failure_rate": round((len(rows) - succeeded) / len(rows), 4),
            "total_llm_calls": sum(row["llm_call_count"] for row in rows),
            "total_protocol_errors": sum(row["error_count"] for row in rows),
            "writeback_turns": sum(row["writeback_applied"] for row in rows),
            "latency_p50_seconds": percentile(latencies, 0.5),
            "latency_p95_seconds": percentile(latencies, 0.95),
            "lexical_state_checks_passed": sum(checks.values()),
            "lexical_state_checks_total": len(checks),
        },
    }


def main() -> int:
    started = now()
    status, health = request("GET", "/api/v1/health")
    if status != 200 or not health:
        raise RuntimeError("Isolated benchmark API is not healthy")
    results = []
    for model, source in MODEL_SOURCES.items():
        results.append(test_model(model, provider_config(model, source)))
    report = {
        "schema_version": "1.0.0",
        "run_kind": "real_provider_fixed_roleplay_state_machine_blackbox",
        "started_at": started,
        "completed_at": now(),
        "variables_fixed": {
            "character_card_sha256": hashlib.sha256(json.dumps(card(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "turn_script_sha256": hashlib.sha256(json.dumps(TURNS, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "temperature": 0.0,
            "max_tokens": 768,
            "protocol": "strict/default-isolated-runtime",
            "retrieval": "chat+structured_memory+temporal enabled; knowledge and vector disabled",
        },
        "limits": [
            "This is a product black-box reliability and lexical-state test, not a prose-quality ranking.",
            "No LLM-as-judge or human preference score is used.",
            "CharacterEval is a separate open Chinese role-playing benchmark and is not represented as a score here.",
        ],
        "results": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "models": [item.get("model") for item in results]}, ensure_ascii=False))
    failed = any(
        item.get("setup_failed")
        or int((item.get("metrics") or {}).get("successful_turns") or 0) != len(TURNS)
        for item in results
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
