"""Run two real Gemma roleplay trials using only Q lines from a local quality sample.

The sample's answers are never read into the model context.  They are not copied
to the report either; only its user-input lines and newly generated responses are
stored for evaluation.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.role_runtime import compact_system_prompt, compact_turn_directive


SAMPLE_PATH = Path(r"A:\RAG\对话集\范例1.txt")
DESKTOP_SETTINGS = Path(r"A:\Mindspace\data\config\settings.json")
REPORTS = ROOT / "reports"
MODEL = os.environ.get("MINDSPACE_LIVE_MODEL", "google/gemma-4-12B-it")
BASE_URL = os.environ.get("MINDSPACE_LIVE_BASE_URL", "https://api.siliconflow.com/v1").rstrip("/")
RUNS = 2


def sample_questions() -> list[str]:
    text = SAMPLE_PATH.read_text(encoding="utf-8")
    return [match.group(1).strip() for line in text.splitlines() if (match := re.match(r"^Q[:：]?(.*)$", line.strip())) and match.group(1).strip()]


def desktop_api_key() -> str:
    environment_key = os.environ.get("MINDSPACE_LIVE_API_KEY", "").strip() or os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if environment_key:
        return environment_key
    settings = json.loads(DESKTOP_SETTINGS.read_text(encoding="utf-8-sig"))
    candidates = [
        settings.get("llm", {}).get("api_key"),
        settings.get("api_key"),
        settings.get("providers", {}).get("siliconflow", {}).get("api_key"),
    ]
    key = next((value for value in candidates if isinstance(value, str) and value.strip()), "")
    if not key:
        raise RuntimeError("Desktop settings do not contain an API key for the live compatibility trial.")
    return key


def call_gemma(api_key: str, messages: list[dict[str, str]]) -> tuple[str, float]:
    payload = json.dumps(
        {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.45,
            "max_tokens": 260,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with request.urlopen(req, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    elapsed = round(time.perf_counter() - started, 3)
    return data["choices"][0]["message"]["content"].strip(), elapsed


def normalize(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]+", text))


def jaccard(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    return round(len(a & b) / len(a | b), 4) if a or b else 1.0


def run_trial(api_key: str, questions: list[str], run_id: str) -> dict[str, object]:
    role_state = {
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
    messages = [{"role": "system", "content": compact_system_prompt(role_state)}]
    turns: list[dict[str, object]] = []
    for number, user_text in enumerate(questions, start=1):
        turn_messages = [*messages, {"role": "system", "content": compact_turn_directive(role_state)}, {"role": "user", "content": user_text}]
        try:
            reply, elapsed = call_gemma(api_key, turn_messages)
            error = None
        except Exception as exc:  # report failures without exposing credentials
            reply, elapsed, error = "", 0.0, f"{type(exc).__name__}: {exc}"
        turns.append({"turn": number, "user": user_text, "reply": reply, "latency_seconds": elapsed, "error": error})
        if error:
            break
        messages.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])
    successful = [turn for turn in turns if not turn["error"]]
    return {
        "run": run_id,
        "turns": turns,
        "successful_turns": len(successful),
        "failure_rate": round(1 - len(successful) / len(questions), 4),
        "latency_mean_seconds": round(statistics.mean(turn["latency_seconds"] for turn in successful), 3) if successful else None,
        "reply_chars_mean": round(statistics.mean(len(turn["reply"]) for turn in successful), 1) if successful else None,
    }


def main() -> None:
    questions = sample_questions()
    if len(questions) != 7:
        raise RuntimeError(f"Expected 7 Q lines, got {len(questions)}.")
    api_key = desktop_api_key()
    trials = [run_trial(api_key, questions, f"run_{index}") for index in range(1, RUNS + 1)]
    similarities = [
        jaccard(trials[0]["turns"][index]["reply"], trials[1]["turns"][index]["reply"])
        for index in range(len(questions))
    ]
    report = {
        "test": "gemma_sample_user_inputs_only",
        "date": str(date.today()),
        "model": MODEL,
        "base_url": BASE_URL,
        "configuration": {
            "temperature": 0.45,
            "max_tokens": 260,
            "runs": RUNS,
            "role_state": "synthetic spouse card; current compact Mindspace prompt functions",
            "sample_handling": "Only Q lines are sent as ordinary user turns. Sample answers are never sent to the model or written to this report.",
        },
        "trials": trials,
        "cross_run": {"mean_jaccard_similarity": round(statistics.mean(similarities), 4), "per_turn_similarity": similarities},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS / "live-gemma-4-12b-sample-user-inputs-2026-08-09.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Gemma 4 sample-user-input roleplay trial", "", "Only the sample Q lines were used. Its A lines were neither sent nor copied.", ""]
    for trial in trials:
        lines.extend([f"## {trial['run']}", ""])
        for turn in trial["turns"]:
            lines.extend([f"### {turn['turn']}. User", str(turn["user"]), "", "### Gemma", str(turn["reply"] or turn["error"]), ""])
    markdown_path = REPORTS / "live-gemma-4-12b-sample-user-inputs-2026-08-09.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "runs": [{key: trial[key] for key in ("run", "successful_turns", "failure_rate", "latency_mean_seconds", "reply_chars_mean")} for trial in trials], "cross_run": report["cross_run"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
