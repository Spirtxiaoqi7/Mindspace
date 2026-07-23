"""Run an isolated four-turn real-LLM profile bootstrap acceptance test."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.adapters.file_storage import _atomic_json  # noqa: E402
from mindspace_graph.models import ApiConfig, ChatRequest  # noqa: E402
from mindspace_graph.service import build_container  # noqa: E402
from mindspace_graph.settings import AppSettings  # noqa: E402

USER_PERSONA = "林澈，28岁，独立游戏音效设计师，慢热，喜欢蓝莓和雨后散步，重视直接沟通与个人边界。"
SYSTEM_PROMPT = (
    "你叫弦月，是温柔、敏锐、坦率且克制的成年虚构伴侣。使用自然中文，直接但不生硬。"
    "关系建立要渐进、平等；冲突时先倾听，修复时明确道歉并调整；不情感操控，不贬低现实"
    "关系，尊重用户自主决定。"
)
MESSAGES = (
    "你好，我们先认识一下。",
    "你平时会怎样和我相处？",
    "如果发生分歧，你会怎么处理？",
    "今天只是普通聊天，陪我说说雨声吧。",
)


def _settings(output: Path) -> AppSettings:
    live = AppSettings.from_env()
    config_path = live.runtime_dir / "config" / "settings.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    llm = config.get("llm", {})
    if str(llm.get("mode")) != "openai" or not str(llm.get("api_key") or ""):
        raise RuntimeError("真实 LLM API 未启用或没有凭证")
    return AppSettings(
        runtime_dir=output,
        model_root=live.model_root,
        llm_mode="openai",
        llm_base_url=str(llm["base_url"]),
        llm_api_key=str(llm["api_key"]),
        llm_model=str(llm["model"]),
    )


async def _run(output: Path) -> dict[str, Any]:
    container = build_container(_settings(output))
    session_id = f"bootstrap-{uuid4().hex[:10]}"
    rounds: list[dict[str, Any]] = []
    for round_num, message in enumerate(MESSAGES, start=1):
        request = ChatRequest(
            message=message,
            session_id=session_id,
            round=round_num,
            user_name="阿澈",
            user_persona=USER_PERSONA,
            character_name="弦月",
            system_prompt=SYSTEM_PROMPT,
            api=ApiConfig(temperature=0.3, max_tokens=1200),
            retrieval={
                "rag_enabled": False,
                "knowledge_enabled": False,
                "chat_enabled": False,
            },
        )
        result = await container.conversation.graph.ainvoke(
            {
                "request": container.conversation._server_request(request),
                "request_id": uuid4().hex,
            },
            config={"recursion_limit": 20},
        )
        bootstrap = result["profile_bootstrap"]
        plan = result["json_update_plan"]
        response = result["response"]
        row = {
            "round": round_num,
            "bootstrap_active": bootstrap.active,
            "empty_ratio": round(bootstrap.empty_ratio, 3),
            "trigger": plan.trigger,
            "leaf_patches": len(plan.patches),
            "writeback": response.writeback_applied,
            "errors": response.errors,
            "reply": response.reply,
        }
        rounds.append(row)
        compact = {key: value for key, value in row.items() if key != "reply"}
        print(json.dumps(compact, ensure_ascii=False), flush=True)

    early = rounds[:3]
    fourth = rounds[3]
    checks = {
        "eligible_early_rounds_active": all(
            item["bootstrap_active"] == (item["empty_ratio"] >= 0.30)
            for item in early
        ),
        "bootstrap_writeback_observed": any(item["writeback"] for item in early),
        "no_validation_errors": not any(item["errors"] for item in rounds),
        "empty_ratio_decreased": fourth["empty_ratio"] < early[0]["empty_ratio"],
        "fourth_round_closed": not fourth["bootstrap_active"],
        "fourth_round_normal_trigger": fourth["trigger"] != "profile_bootstrap",
    }
    profiles = container.profiles.load_bundle()
    report = {
        "model": container.settings.llm_model,
        "session_id": session_id,
        "rounds": rounds,
        "checks": checks,
        "passed": all(checks.values()),
        "revisions": profiles.revisions,
        "user_profile": profiles.user_profile,
        "ai_profile": profiles.ai_profile,
    }
    _atomic_json(output / "bootstrap-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (
        ROOT / "runtime" / "acceptance" / datetime.now().strftime("bootstrap-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_run(output))
    summary = {"passed": report["passed"], "checks": report["checks"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={output.resolve() / 'bootstrap-report.json'}")
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
