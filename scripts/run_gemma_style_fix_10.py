"""One live Gemma regression after the dialogue-first role prompt change."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.role_runtime import compact_system_prompt, compact_turn_directive
from run_gemma_sample_inputs import BASE_URL, MODEL, call_gemma, desktop_api_key, sample_questions


def main() -> None:
    questions = sample_questions() + [
        "今天累不累？",
        "那我们一起吃个晚饭吧。",
        "我会认真对待我们的关系。",
    ]
    state = {
        "character_name": "苏澄",
        "user_name": "赵阳",
        "user_alias": "老公",
        "relationship": "夫妻",
        "description": "苏澄是赵阳的妻子，和他共同生活。",
        "personality": "温柔而坚定，表达直接；会接住情绪，也会在必要时讲清现实边界。",
        "scenario": "两人在日常聊天中互相关心、开玩笑，也认真讨论生活里的重要决定。",
        "preferences": [],
        "tasks": [],
    }
    api_key = desktop_api_key()
    messages = [{"role": "system", "content": compact_system_prompt(state)}]
    turns = []
    for number, user_text in enumerate(questions, start=1):
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
        turns.append(
            {
                "turn": number,
                "user": user_text,
                "reply": reply,
                "latency_seconds": latency,
                "error": error,
                "attempts": attempts,
                "starts_with_parenthetical_action": bool(re.match(r"^\s*[（(]", reply)),
            }
        )
        if error:
            break
        messages.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])
    successful = [turn for turn in turns if not turn["error"]]
    report = {
        "test": "gemma_dialogue_first_style_fix_10_turns",
        "date": str(date.today()),
        "configuration": {
            "model": MODEL,
            "base_url": BASE_URL,
            "temperature": 0.45,
            "max_tokens": 260,
            "prompt_change": "dialogue first; no leading parenthetical action; real-world ability boundary",
            "sample_handling": "The first seven ordinary user turns are Q lines only. No sample answer is sent or copied.",
        },
        "turns": turns,
        "summary": {
            "successful_turns": len(successful),
            "failure_rate": round(1 - len(successful) / len(questions), 4),
            "leading_parenthetical_actions": sum(turn["starts_with_parenthetical_action"] for turn in successful),
            "network_retries": sum(turn["attempts"] - 1 for turn in turns),
            "mean_latency_seconds": round(sum(turn["latency_seconds"] for turn in successful) / len(successful), 3) if successful else None,
            "mean_reply_chars": round(sum(len(turn["reply"]) for turn in successful) / len(successful), 1) if successful else None,
        },
    }
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    model_slug = re.sub(r"[^a-z0-9]+", "-", MODEL.lower()).strip("-")
    json_path = reports / f"live-{model_slug}-dialogue-first-10-turns-2026-08-09.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = [f"# {MODEL} dialogue-first regression", ""]
    for turn in turns:
        markdown.extend([f"## {turn['turn']}. User", turn["user"], "", "### Gemma", turn["reply"] or turn["error"], ""])
    markdown_path = reports / f"live-{model_slug}-dialogue-first-10-turns-2026-08-09.md"
    markdown_path.write_text("\n".join(markdown), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
