"""Run the four bounded Mindspace 0.8.2 regressions with desktop DeepSeek settings."""

from __future__ import annotations

import asyncio
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mindspace_graph.models import ChatRequest
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_CONFIG = Path(r"A:\Mindspace\data\config\settings.json")


class _CandidateCapture:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.last_stream = ""

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def stream(self, messages, config):
        chunks: list[str] = []
        for chunk in self.inner.stream(messages, config):
            chunks.append(chunk)
            yield chunk
        self.last_stream = "".join(chunks)


def _prepare_runtime() -> tuple[Path, dict]:
    source = json.loads(DESKTOP_CONFIG.read_text(encoding="utf-8"))
    llm = source.get("llm", {})
    if str(llm.get("base_url") or "").rstrip("/") != "https://api.deepseek.com":
        raise RuntimeError("desktop LLM base URL is not the official DeepSeek endpoint")
    if not str(llm.get("api_key") or ""):
        raise RuntimeError("desktop DeepSeek API key is not configured")

    runtime = ROOT / f".runtime-082-real-api-{uuid4().hex[:10]}"
    (runtime / "config").mkdir(parents=True)
    source.setdefault("capabilities", {})["master_enabled"] = True
    source["capabilities"]["web_search_enabled"] = True
    source["capabilities"]["max_web_results"] = 5
    source["capabilities"]["max_web_pages"] = 5
    source["capabilities"]["max_web_content_chars"] = 8000
    source["llm"]["compaction_enabled"] = False
    source["llm"]["role_audit_enabled"] = False
    (runtime / "config" / "settings.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime, source


def _summary(name: str, response) -> dict:
    tool = response.tool_execution or {}
    return {
        "case": name,
        "status": response.status,
        "model_calls": response.llm_call_count,
        "tool": tool.get("tool"),
        "tool_status": tool.get("status"),
        "result_count": int(tool.get("source_count") or 0),
        "reply_chars": len(response.reply),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-runtime", type=Path)
    args = parser.parse_args()
    desktop = json.loads(DESKTOP_CONFIG.read_text(encoding="utf-8"))
    runtime = args.resume_runtime.resolve() if args.resume_runtime else None
    if runtime is None:
        runtime, desktop = _prepare_runtime()
    settings = AppSettings(
        runtime_dir=runtime,
        model_root=ROOT / "assets" / "models",
        llm_mode="openai",
        role_audit_enabled=False,
        context_compaction_enabled=False,
        tts_provider="browser",
        asr_provider="browser",
    )
    container = build_container(settings)
    capture = _CandidateCapture(container.conversation.dependencies.llm)
    container.conversation.dependencies.llm = capture
    character_id = str(container.characters.default()["character_id"])
    session_id = f"real-082-{uuid4().hex[:10]}"
    report: list[dict] = []
    completed_cases: set[str] = set()
    if args.resume_runtime:
        saved_sessions = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((runtime / "data" / "sessions").glob("real-082-*.json"))
        ]
        ordinary_sessions = [
            item
            for item in saved_sessions
            if any(
                message.get("role") == "user" and "本次回归标记是琥珀-082" in str(message.get("content") or "")
                for message in item.get("messages", [])
            )
        ]
        if len(ordinary_sessions) != 1:
            raise RuntimeError("resume runtime must contain exactly one successful ordinary-chat session")
        saved = ordinary_sessions[0]
        session_id = str(saved["session_id"])
        character_id = str(saved["character_id"])
        if not any(item.get("role") == "assistant" and item.get("round") == 1 for item in saved.get("messages", [])):
            raise RuntimeError("resume runtime has no successful ordinary-chat turn")
        report.append(
            {
                "case": "ordinary",
                "status": "success",
                "model_calls": 1,
                "tool": None,
                "tool_status": None,
                "result_count": 0,
                "reply_chars": len(next(item["content"] for item in saved["messages"] if item.get("role") == "assistant" and item.get("round") == 1)),
            }
        )
        completed_cases.add("ordinary")
        for name in ("memory", "web", "task"):
            successful = next(
                (
                    message
                    for item in saved_sessions
                    for message in item.get("messages", [])
                    if message.get("role") == "assistant"
                    and (message.get("tool_execution") or {}).get("tool") == name
                    and (message.get("tool_execution") or {}).get("status") == "success"
                ),
                None,
            )
            if successful is not None:
                tool = successful["tool_execution"]
                report.append(
                    {
                        "case": name,
                        "status": "success",
                        "model_calls": 3 if name == "task" else 2,
                        "tool": name,
                        "tool_status": "success",
                        "result_count": int(tool.get("source_count") or 0),
                        "reply_chars": len(str(successful.get("content") or "")),
                    }
                )
                completed_cases.add(name)
    cases = [
        (
            "ordinary",
            "本次回归标记是琥珀-082。请自然确认收到，不使用任何工具。",
            1,
            1,
            None,
        ),
        (
            "memory",
            "必须使用 memory 工具查询“琥珀-082”，收到结果后只报告是否找到。",
            2,
            2,
            "memory",
        ),
        (
            "web",
            "必须使用 web 工具读取 https://api-docs.deepseek.com/，收到结果后只说明是否获得来源。",
            3,
            2,
            "web",
        ),
        (
            "task",
            "必须使用 task 工具创建任务“交付 0.8.2 回归报告”，截止时间为 2026-08-11T18:00:00+08:00。",
            4,
            3,
            "task",
        ),
    ]
    try:
        for name, message, round_num, expected_calls, expected_tool in cases:
            if name in completed_cases:
                continue
            case_session_id = session_id if name == "ordinary" else f"{session_id}-{name}-success-{uuid4().hex[:6]}"
            case_round = round_num if name == "ordinary" else 1
            response = await container.conversation.invoke(
                ChatRequest(
                    message=message,
                    session_id=case_session_id,
                    character_id=character_id,
                    round=case_round,
                    client_timezone="Asia/Shanghai",
                    client_utc_offset_minutes=480,
                ),
                request_id=f"082-{name}-{uuid4().hex}",
            )
            if response.status != "success":
                raise RuntimeError(f"{name} failed: {response.errors}")
            if response.llm_call_count != expected_calls:
                diagnostic = runtime / f"diagnostic-{name}-candidate.txt"
                diagnostic.write_text(capture.last_stream, encoding="utf-8")
                raise RuntimeError(f"{name} used {response.llm_call_count} model calls, expected {expected_calls}")
            if expected_tool is None:
                if response.tool_execution:
                    raise RuntimeError("ordinary chat unexpectedly executed a tool")
            else:
                tool = response.tool_execution or {}
                if tool.get("tool") != expected_tool or tool.get("status") != "success":
                    raise RuntimeError(f"{name} did not complete {expected_tool}: {tool}")
                if name in {"memory", "web"} and int(tool.get("source_count") or 0) < 1:
                    raise RuntimeError(f"{name} returned no data")
                if name == "task" and not bool((tool.get("receipt") or {}).get("changed")):
                    raise RuntimeError("task command did not change authoritative data")
            report.append(_summary(name, response))
    finally:
        container.conversation.close()

    output = {
        "tested_at": datetime.now(UTC).isoformat(),
        "runtime": str(runtime),
        "provider": "DeepSeek official",
        "base_url": "https://api.deepseek.com",
        "model": str(desktop.get("llm", {}).get("model") or ""),
        "cases": report,
        "secrets_recorded": False,
    }
    output_path = ROOT / "reports" / "mindspace-0.8.2-real-api-regression.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
