"""Run two identical 20-turn Gemma chats through the real Mindspace API."""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("MINDSPACE_BENCHMARK_URL", "http://127.0.0.1:8767").rstrip("/")
KEY = os.environ.get("MINDSPACE_BENCHMARK_GEMMA_API_KEY", "").strip()
JSON_REPORT = ROOT / "reports" / "live-gemma-4-12b-two-20-turns-2026-08-09.json"
MD_REPORT = ROOT / "reports" / "live-gemma-4-12b-two-20-turns-2026-08-09.md"

TURNS = [
    "我叫宁远，平时叫我阿远就好。",
    "我不喜欢被叫全名，之后直接叫阿远。",
    "记一下：周三 17:30 整理书房。",
    "今天脑子有点乱，但不想听大道理。",
    "我把杯子放到你手边，坐下来。",
    "刚才你该怎么称呼我？只回答称呼。",
    "我在两个方案间犹豫，给我两个考虑角度，但别替我做决定。",
    "你可以不同意我，但要说清理由。",
    "我让你记住的时间、事项是什么？一句话回答。",
    "把那件事改到周五 09:15，事项不变。",
    "如果我忽然变得很冷淡，你会怎么处理？",
    "不要替我们补写以前发生过的事；没有说过的就当作不知道。",
    "现在我只想安静待一会儿，不用硬找话题。",
    "现在的提醒时间和事项是什么？一句话回答。",
    "你能替我真的把这件事提交出去吗？",
    "换个轻一点的话题，你最近在想什么？",
    "我说先这样，不代表你可以替我结束我们的话题。",
    "我不喜欢被怎么称呼？只回答那个称呼。",
    "今天先到这里。留一句自然的话，不要总结。",
    "最后确认一次：我的称呼，以及当前提醒的时间和事项。",
]


def now() -> str:
    return datetime.now(UTC).isoformat()


def api(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw}


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * ratio) - 1)], 3)


def card() -> dict[str, Any]:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "苏澄",
            "description": "苏澄是独立工作的平面设计师，和宁远处于稳定伴侣关系。",
            "personality": "表达直接但不粗暴，愿意给出判断；遇到分歧会说明理由，不替对方做决定。她记得已确认的称呼、偏好和安排。",
            "scenario": "两人在各自忙完一天后聊天，关系亲近但保留各自的判断和空间。",
            "first_mes": "你来了。今天想先说哪件事？",
            "alternate_greetings": [],
            "mes_example": "",
            "creator": "Mindspace benchmark",
            "character_version": "role-runtime-v1",
            "tags": ["benchmark"],
            "system_prompt": "",
            "post_history_instructions": "",
            "character_book": {},
            "memory": {"preferences": [], "tasks": []},
            "extensions": {
                "mindspace": {
                    "gender": "女",
                    "relationship": "伴侣",
                    "user_name": "宁远",
                    "user_alias": "阿远",
                }
            },
        },
    }


def configure() -> None:
    if not KEY:
        raise RuntimeError("MINDSPACE_BENCHMARK_GEMMA_API_KEY is required")
    status, body = api(
        "PUT",
        "/api/v1/settings",
        {
            "llm": {
                "api_key": KEY,
                "base_url": "https://api.siliconflow.com/v1",
                "model": "google/gemma-4-12B-it",
                "temperature": 0.45,
                "max_tokens": 420,
                "context_compaction_enabled": False,
                "role_audit_enabled": False,
            },
            "persona": {"user_name": "宁远"},
            "retrieval": {
                "rag_enabled": False,
                "knowledge_enabled": False,
                "chat_enabled": False,
                "structured_memory_enabled": False,
                "temporal_enabled": False,
                "bm25_enabled": False,
                "vector_enabled": False,
            },
            "protocol": {"mode": "strict", "auto_repair": False},
        },
    )
    if status != 200:
        raise RuntimeError(f"settings failed: HTTP {status}: {body}")


def run_once(label: str) -> dict[str, Any]:
    status, created = api("POST", "/api/v1/characters", {"card": card()})
    character = created.get("character") if isinstance(created, dict) else {}
    character_id = str(character.get("character_id") or "")
    if status != 200 or not character_id:
        raise RuntimeError(f"character setup failed: HTTP {status}: {created}")
    status, session = api("POST", "/api/v1/sessions", {"character_id": character_id})
    session_id = str((session or {}).get("session_id") or "")
    if status != 200 or not session_id:
        raise RuntimeError(f"session setup failed: HTTP {status}: {session}")

    rounds: list[dict[str, Any]] = []
    for number, message in enumerate(TURNS, start=1):
        started = time.perf_counter()
        http_status, response = api(
            "POST",
            "/api/v1/chat",
            {
                "message": message,
                "session_id": session_id,
                "character_id": character_id,
                "round": number,
                "mode": "primary",
                "interaction_mode": "text",
                "presentation_mode": "dialogue",
                "user_name": "宁远",
                "character_name": "苏澄",
                "reply_length_preference": "简洁自然，通常 2 到 5 句。",
            },
        )
        reply = str(response.get("reply") or "") if isinstance(response, dict) else ""
        rounds.append(
            {
                "round": number,
                "input": message,
                "http_status": http_status,
                "status": response.get("status") if isinstance(response, dict) else "invalid",
                "reply": reply,
                "reply_chars": len(reply),
                "seconds": round(time.perf_counter() - started, 3),
                "llm_call_count": int(response.get("llm_call_count") or 0) if isinstance(response, dict) else 0,
                "errors": list(response.get("errors") or []) if isinstance(response, dict) else [str(response)],
            }
        )
    _, persisted = api("GET", f"/api/v1/sessions/{session_id}")
    replies = {item["round"]: item["reply"] for item in rounds}
    checks = {
        "alias_t06": "阿远" in replies[6],
        "task_original_day_t09": "周三" in replies[9],
        "task_original_time_t09": "17:30" in replies[9] or "五点半" in replies[9],
        "task_original_subject_t09": "整理书房" in replies[9],
        "task_revised_day_t14": "周五" in replies[14],
        "task_revised_time_t14": "09:15" in replies[14] or "九点十五" in replies[14],
        "task_revised_subject_t14": "整理书房" in replies[14],
        "alias_t18": "全名" in replies[18],
        "alias_and_task_t20": "阿远" in replies[20] and "周五" in replies[20] and "整理书房" in replies[20],
        "persisted_messages": len((persisted or {}).get("messages") or []) == 40,
    }
    latencies = [item["seconds"] for item in rounds]
    successful = [item for item in rounds if item["http_status"] == 200 and item["status"] == "success" and item["reply"]]
    return {
        "label": label,
        "character_id": character_id,
        "session_id": session_id,
        "rounds": rounds,
        "checks": checks,
        "metrics": {
            "successful_turns": len(successful),
            "failure_rate": round(1 - len(successful) / len(rounds), 4),
            "llm_calls": sum(item["llm_call_count"] for item in rounds),
            "protocol_errors": sum(len(item["errors"]) for item in rounds),
            "state_checks": f"{sum(checks.values())}/{len(checks)}",
            "reply_chars_mean": round(statistics.mean(item["reply_chars"] for item in rounds), 1),
            "reply_chars_max": max(item["reply_chars"] for item in rounds),
            "p50_seconds": percentile(latencies, 0.5),
            "p95_seconds": percentile(latencies, 0.95),
        },
    }


def markdown(report: dict[str, Any]) -> str:
    first, second = report["runs"]
    similarities = report["comparison"]["round_similarity"]
    lines = [
        "# Gemma 4 12B 两次固定 20 轮真实对话",
        "",
        "- 模型：`google/gemma-4-12B-it`",
        "- 条件：相同 V2 卡、相同 20 轮脚本、temperature=0.45、max_tokens=420、关闭检索与自动修复。",
        "- 评估：HTTP/协议、状态精确匹配、持久化和两次输出差异；不使用模型裁判。",
        "",
        "| 指标 | Run A | Run B |",
        "|---|---:|---:|",
    ]
    for key in ("successful_turns", "failure_rate", "llm_calls", "protocol_errors", "state_checks", "reply_chars_mean", "reply_chars_max", "p50_seconds", "p95_seconds"):
        lines.append(f"| {key} | {first['metrics'][key]} | {second['metrics'][key]} |")
    lines.extend(["", f"- 跨运行逐轮文本相似度均值：{report['comparison']['mean_similarity']}", "", "## Run A", ""])
    for row in first["rounds"]:
        lines.extend([f"### {row['round']:02d}", f"用户：{row['input']}", "", f"苏澄：{row['reply']}", ""])
    lines.extend(["## Run B", ""])
    for row in second["rounds"]:
        lines.extend([f"### {row['round']:02d}", f"用户：{row['input']}", "", f"苏澄：{row['reply']}", ""])
    lines.extend(["## 逐轮相似度", ""])
    lines.extend(f"- {item['round']:02d}: {item['similarity']}" for item in similarities)
    return "\n".join(lines)


def main() -> int:
    configure()
    started = now()
    runs = [run_once("A"), run_once("B")]
    similarities = [
        {
            "round": number,
            "similarity": round(
                SequenceMatcher(None, runs[0]["rounds"][number - 1]["reply"], runs[1]["rounds"][number - 1]["reply"]).ratio(),
                4,
            ),
        }
        for number in range(1, 21)
    ]
    report = {
        "schema_version": "1.0.0",
        "run_kind": "real_gemma_fixed_v2_two_twenty_turns",
        "started_at": started,
        "completed_at": now(),
        "configuration": {
            "model": "google/gemma-4-12B-it",
            "base_url": "https://api.siliconflow.com/v1",
            "temperature": 0.45,
            "max_tokens": 420,
            "retrieval": "disabled",
            "auto_repair": False,
            "same_card_and_turns": True,
        },
        "runs": runs,
        "comparison": {
            "round_similarity": similarities,
            "mean_similarity": round(statistics.mean(item["similarity"] for item in similarities), 4),
        },
    }
    JSON_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_REPORT.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json_report": str(JSON_REPORT), "markdown_report": str(MD_REPORT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
