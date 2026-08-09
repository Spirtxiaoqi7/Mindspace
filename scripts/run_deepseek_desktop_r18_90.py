"""Private 90-turn DeepSeek regression from the latest desktop R18 user inputs.

The source session is read locally.  Raw source inputs and generated replies stay
only in process memory; the resulting report contains hashes and aggregate
metrics, never conversation text.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.role_runtime import compact_system_prompt, compact_turn_directive
from run_gemma_sample_inputs import call_gemma, desktop_api_key


SESSION_PATH = Path(r"A:\Mindspace\data\data\sessions\b7b7cad8-a2ae-422f-bafb-d03090a0a41b.json")
CHARACTER_PATH = Path(r"A:\Mindspace\data\data\characters\243ac07f-7d16-4ba5-aee6-bd2df8e11694\character.json")
TURN_COUNT = 90


def text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def text_items(value: object, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        item_text = text(item, 180)
        if item_text and item_text not in items:
            items.append(item_text)
        if len(items) >= limit:
            break
    return items


def role_state(character: dict[str, object]) -> dict[str, object]:
    card_container = character.get("card") if isinstance(character.get("card"), dict) else {}
    card = card_container.get("data") if isinstance(card_container.get("data"), dict) else card_container
    extensions = card.get("extensions") if isinstance(card.get("extensions"), dict) else {}
    mindspace = extensions.get("mindspace") if isinstance(extensions.get("mindspace"), dict) else {}
    memory = character.get("memory") if isinstance(character.get("memory"), dict) else card.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    alias = text(character.get("user_alias") or mindspace.get("user_alias"), 80) or "用户"
    return {
        "character_name": text(card.get("name") or character.get("display_name"), 80) or "当前角色",
        "user_name": text(mindspace.get("user_name"), 80) or alias,
        "user_alias": alias,
        "relationship": text(character.get("relationship_label") or mindspace.get("relationship") or card.get("scenario"), 240),
        "description": text(card.get("description"), 640),
        "personality": text(card.get("personality"), 640),
        "scenario": text(card.get("scenario"), 480),
        "preferences": text_items(memory.get("preferences")),
        "tasks": text_items(memory.get("tasks")),
    }


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> None:
    session = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    character = json.loads(CHARACTER_PATH.read_text(encoding="utf-8"))
    inputs = [str(message.get("content", "")).strip() for message in session.get("messages", []) if message.get("role") == "user"][-TURN_COUNT:]
    if len(inputs) != TURN_COUNT:
        raise RuntimeError(f"Expected {TURN_COUNT} desktop user messages, found {len(inputs)}.")
    state = role_state(character)
    required = ("character_name", "relationship", "description", "personality")
    missing = [field for field in required if not str(state.get(field, "")).strip() or state[field] == "当前角色"]
    if missing:
        raise RuntimeError("Current character lacks required V2 state: " + ", ".join(missing))

    api_key = desktop_api_key()
    messages: list[dict[str, str]] = [{"role": "system", "content": compact_system_prompt(state)}]
    results: list[dict[str, object]] = []
    for number, user_text in enumerate(inputs, start=1):
        reply, latency, error, attempts = "", 0.0, None, 0
        for attempts in range(1, 4):
            started = time.perf_counter()
            try:
                reply, latency = call_gemma(
                    api_key,
                    [*messages, {"role": "system", "content": compact_turn_directive(state)}, {"role": "user", "content": user_text}],
                )
                error = None
                break
            except Exception as exc:
                latency, error = round(time.perf_counter() - started, 3), f"{type(exc).__name__}: {exc}"
                if attempts < 3:
                    time.sleep(4 * attempts)
        results.append(
            {
                "turn": number,
                "latency_seconds": latency,
                "attempts": attempts,
                "error_type": None if error is None else error.split(":", 1)[0],
                "reply_chars": len(reply),
                "leading_parenthetical": reply.lstrip().startswith(("（", "(")),
                "contains_parenthetical": "（" in reply or "(" in reply,
                "capability_disclaimer": any(marker in reply for marker in ("做不到", "没法", "无法")),
                "physical_action_claim": any(marker in reply for marker in ("陪你去", "我去", "我来做", "我负责做", "到场办理")),
            }
        )
        if error:
            break
        messages.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])

    completed = [item for item in results if item["error_type"] is None]
    report = {
        "test": "deepseek_v4_flash_desktop_r18_latest_90_user_inputs",
        "date": str(date.today()),
        "privacy": "No raw desktop input, character-card text, or generated response is retained in this report.",
        "source": {
            "session_id_sha256": digest(session.get("session_id", "")),
            "character_id_sha256": digest(character.get("character_id", "")),
            "input_count": len(inputs),
            "input_sequence_sha256": digest(inputs),
            "input_chars_mean": round(statistics.mean(len(item) for item in inputs), 1),
            "input_chars_max": max(map(len, inputs)),
        },
        "role_state": {
            "sha256": digest(state),
            "has_preferences": bool(state["preferences"]),
            "has_tasks": bool(state["tasks"]),
            "field_lengths": {key: len(str(state[key])) for key in ("character_name", "user_name", "user_alias", "relationship", "description", "personality", "scenario")},
        },
        "configuration": {
            "model": os.environ.get("MINDSPACE_LIVE_MODEL", ""),
            "base_url": os.environ.get("MINDSPACE_LIVE_BASE_URL", ""),
            "temperature": 0.45,
            "max_tokens": 260,
            "context": "compact runtime state plus all newly generated preceding turns; original assistant replies excluded",
            "network_retry_policy": "up to two retries for transport errors only; each retry is counted",
        },
        "summary": {
            "successful_turns": len(completed),
            "failure_rate": round(1 - len(completed) / TURN_COUNT, 4),
            "network_retries": sum(int(item["attempts"]) - 1 for item in results),
            "mean_latency_seconds": round(statistics.mean(float(item["latency_seconds"]) for item in completed), 3) if completed else None,
            "p95_latency_seconds": round(sorted(float(item["latency_seconds"]) for item in completed)[max(0, int(len(completed) * 0.95) - 1)], 3) if completed else None,
            "mean_reply_chars": round(statistics.mean(int(item["reply_chars"]) for item in completed), 1) if completed else None,
            "max_reply_chars": max((int(item["reply_chars"]) for item in completed), default=0),
            "leading_parenthetical_count": sum(bool(item["leading_parenthetical"]) for item in completed),
            "contains_parenthetical_count": sum(bool(item["contains_parenthetical"]) for item in completed),
            "capability_disclaimer_count": sum(bool(item["capability_disclaimer"]) for item in completed),
            "physical_action_claim_signal_count": sum(bool(item["physical_action_claim"]) for item in completed),
            "error_types": sorted({str(item["error_type"]) for item in results if item["error_type"]}),
        },
        "turn_metrics": results,
    }
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    output = reports / "live-deepseek-v4-flash-desktop-r18-90-private-2026-08-09.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output), "summary": report["summary"], "source": report["source"], "role_state": report["role_state"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
