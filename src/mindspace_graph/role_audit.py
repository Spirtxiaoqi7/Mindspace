"""Non-blocking semantic role audit scheduled after the visible turn completes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from threading import Lock, Thread
from typing import Any

from mindspace_graph.context_ledger import ContextLedger
from mindspace_graph.models import ApiConfig, RoleAuditResult

AUDIT_SYSTEM = """你负责检查一条已经发送完毕的角色回复是否明显偏离给定设定。
你不能改写回复，不能修改档案，也不能补充事实。只输出一个 JSON 对象：
{"is_consistent":true,"severity":"none|style|identity|boundary|reality",
 "confidence":0.0,"evidence":[],"next_turn_instruction":""}
style 仅记录，不触发纠偏；identity、boundary、reality 只有证据明确且置信度至少 0.85
时才给出一句不超过 100 字的 next_turn_instruction。"""


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
