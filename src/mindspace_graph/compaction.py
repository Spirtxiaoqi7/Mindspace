"""Low-priority context compaction kept outside the conversational graph."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from mindspace_graph.context_ledger import CompactionJob, ContextLedger
from mindspace_graph.models import ApiConfig, ProfileBundle
from mindspace_graph.ports import LanguageModelPort, ProfileRepositoryPort
from mindspace_graph.settings import AppSettings

COMPACTION_SYSTEM_PROMPT = """你负责压缩一段已经结束的对话历史，供后续对话延续语境。
你不是对话角色，不回复用户，不修改人物档案 JSON，也不提出新的事实。
只整理输入中实际出现的对话过程、尚未解决的话题、明确承诺和重要关系事件。
知识库召回、工具说明、系统协议和推测不得写入摘要。
如果输入与已有摘要冲突，以未删除的较新原始对话为准。
只输出一个 JSON 对象，不要 Markdown，不要标签。"""


def build_compaction_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    schema = {
        "summary_version": 1,
        "cutoff_sequence": payload["cutoff_sequence"],
        "dialogue_summary": "简洁但完整的对话进展",
        "open_threads": [],
        "commitments": [],
        "relationship_events": [],
    }
    return [
        {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "输出结构示例：\n"
                f"{json.dumps(schema, ensure_ascii=False)}\n\n"
                "待压缩数据：\n"
                f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
            ),
        },
    ]


def parse_compaction_output(raw: str, cutoff_sequence: int) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("compaction response did not contain a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("compaction response must be an object")
    summary = str(value.get("dialogue_summary") or "").strip()
    if not summary:
        raise ValueError("compaction dialogue_summary is blank")
    result = {
        "summary_version": 1,
        "cutoff_sequence": cutoff_sequence,
        "dialogue_summary": summary,
    }
    for key in ("open_threads", "commitments", "relationship_events"):
        raw_items = value.get(key)
        result[key] = (
            [str(item).strip() for item in raw_items if str(item).strip()][-24:]
            if isinstance(raw_items, list)
            else []
        )
    return result


class ContextCompactionService:
    """Schedules durable work and executes at most one compaction call at a time."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        ledger: ContextLedger,
        profiles: ProfileRepositoryPort,
        llm_provider: Callable[[], LanguageModelPort],
        active_run_count: Callable[[], int],
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.profiles = profiles
        self.llm_provider = llm_provider
        self.active_run_count = active_run_count
        self._runner: asyncio.Task[None] | None = None
        self._gate = asyncio.Lock()

    def _api_config(self) -> ApiConfig:
        return ApiConfig(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            model=self.settings.context_compaction_model or self.settings.llm_model,
            temperature=0,
            max_tokens=self.settings.context_compaction_max_tokens,
        )

    def evaluate_pending(self) -> list[str]:
        if not self.settings.context_compaction_enabled:
            return []
        jobs: list[str] = []
        for session_id in self.ledger.take_compaction_evaluations():
            job_id = self.ledger.enqueue_compaction(
                session_id,
                context_window=self.settings.llm_context_window,
                soft_ratio=self.settings.context_compaction_soft_ratio,
                patch_limit=self.settings.context_compaction_patch_limit,
                retain_recent_turns=self.settings.context_compaction_retain_turns,
                delay_seconds=self.settings.context_compaction_delay_seconds,
            )
            if job_id:
                jobs.append(job_id)
        return jobs

    def kick(self) -> None:
        if not self.settings.context_compaction_enabled:
            return
        self.evaluate_pending()
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run_ready(), name="mindspace-context-compact")

    async def drain(self) -> None:
        """Test/admin hook: evaluate and await currently ready background work."""

        self.kick()
        if self._runner is not None:
            await self._runner

    async def _run_ready(self) -> None:
        async with self._gate:
            while self.active_run_count() == 0:
                job = self.ledger.claim_compaction_job()
                if job is None:
                    delay = self.ledger.next_compaction_delay()
                    if delay is None:
                        return
                    await asyncio.sleep(min(delay, 30.0))
                    continue
                await self._execute(job)

    async def _execute(self, job: CompactionJob) -> None:
        try:
            payload = self.ledger.compaction_input(job)
            messages = build_compaction_messages(payload)
            raw = await asyncio.to_thread(
                self.llm_provider().compact,
                messages,
                self._api_config(),
            )
            summary = parse_compaction_output(raw, job.cutoff_sequence)
            current_profiles: ProfileBundle = self.profiles.load_bundle()
            self.ledger.activate_compaction(job, summary=summary, profiles=current_profiles)
        except Exception as exc:  # noqa: BLE001 - durable retry owns failure semantics
            self.ledger.fail_compaction(job.job_id, str(exc), retry=True)
