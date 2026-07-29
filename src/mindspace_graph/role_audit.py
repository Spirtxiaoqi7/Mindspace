"""Non-blocking semantic role audit scheduled after the visible turn completes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from threading import Lock, Thread
from typing import Any

from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.models import ApiConfig, RoleAuditResult

AUDIT_SYSTEM = """你在一条角色回复发送完毕后执行低优先级连续性整理。
你不能改写已发送回复，不能修改用户档案、AI 档案或运行状态，也不能补充不存在的事实。
你同时完成两件事：
1. 检查回复是否明显偏离角色设定；
2. 用当前用户输入和助手回复压缩本轮已经发生的事件与推进，供下一轮自然承接。
只输出一个 JSON 对象：
{"is_consistent":true,"severity":"none|style|identity|boundary|reality",
 "confidence":0.0,"evidence":[],"next_turn_instruction":"",
 "recent_event_summary":"","event_progression":"","open_threads":[]}
style 仅记录，不触发纠偏；identity、boundary、reality 只有证据明确且置信度至少 0.85
时才给出一句不超过 100 字的 next_turn_instruction。
recent_event_summary 只写本轮明确发生的关系事件，不超过 160 字；
event_progression 只写场景、话题或共同任务从什么状态推进到什么状态，不超过 160 字；
open_threads 最多 3 条，只保留下一轮值得承接且尚未解决的事项。不得把助手自己的猜测写成用户事实。"""


def parse_role_audit(raw: str) -> RoleAuditResult:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("role audit did not return JSON")
        text = match.group(0)
    return RoleAuditResult.model_validate(json.loads(text))


class RoleAuditService:
    """Single low-priority worker; never participates in the foreground graph."""

    def __init__(
        self,
        *,
        ledger: ContextLedger,
        llm_provider: Callable[[], Any],
        api_provider: Callable[[], ApiConfig],
        active_run_count: Callable[[], int],
        enabled: Callable[[], bool],
    ) -> None:
        self.ledger = ledger
        self.llm_provider = llm_provider
        self.api_provider = api_provider
        self.active_run_count = active_run_count
        self.enabled = enabled
        self._lock = Lock()
        self._thread: Thread | None = None

    def kick(self) -> None:
        if not self.enabled() or self.active_run_count() > 0:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(target=self._run, name="mindspace-role-audit", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while self.enabled() and self.active_run_count() == 0:
            job = self.ledger.claim_role_audit()
            if job is None:
                return
            try:
                payload = job["payload"]
                messages = [
                    {"role": "system", "content": AUDIT_SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    },
                ]
                llm = self.llm_provider()
                raw = llm.audit_role(messages, self.api_provider())
                result = parse_role_audit(raw)
                take_usage = getattr(llm, "take_usage", None)
                usage = take_usage() if callable(take_usage) else None
                self.ledger.complete_role_audit(job, result, usage)
            except Exception as exc:  # noqa: BLE001 - durable job is retried
                self.ledger.fail_role_audit(job["job_id"], str(exc))
