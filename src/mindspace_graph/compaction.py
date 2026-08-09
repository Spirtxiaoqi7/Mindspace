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

COMPACTION_SYSTEM_PROMPT = """你负责从一段已经结束的原始对话中提取结构化增量。
你不是对话角色，不回复用户，不修改人物档案 JSON，也不提出新的事实。
只使用 dialogue 中实际出现的消息；previous_summary 只用于识别已有状态和冲突，禁止重新摘要它。
每个事件、事实、承诺、关系变化和未完话题都必须提供 evidence_ids，且只能引用输入消息 ID。
知识库召回、工具说明、系统协议、成人风格指令和无证据推测不得写入摘要。
装饰性动作、神态、镜头语言、括号舞台说明和重复场景铺陈不属于连续性事实，不得进入摘要；
current_scene 只记录用户原话明确确认的地点、时间和正在进行的活动，助手单方面叙述不能建立场景。
情绪只能写成 temporary_cues 并附置信说明，不能升级成稳定人格事实。
通用代词统一使用TA或角色名字。如果输入与已有状态冲突，以未删除的较新原始对话为准。
严格控制输出体积：dialogue_summary 不超过120个中文字符；current_scene 每项不超过30字；
events 最多6项，open_threads/commitments/relationship_deltas 各最多4项，
confirmed_facts 最多6项，temporary_cues 最多3项；每个 text 不超过80个中文字符。
成人段可额外输出 adult_facts：只写已经发生且以后可能被追问的事实结果，禁止文学化复述；
该字段由服务端按成人模式隔离，不得把成人偏好推断成人物档案。
只保留对后续连续性有用的增量，不逐轮复述，不输出空占位项。
只输出一个 JSON 对象，不要 Markdown，不要标签。"""

_ADULT_DETAIL = re.compile(
    r"(?:性交|做爱|口交|手交|插入|抽送|射精|内射|高潮|阴茎|阴道|精液|鸡巴|肉棒|小穴)",
    re.I,
)


def build_compaction_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    schema = {
        "summary_version": 2,
        "dialogue_summary": "120字内，只概括本段新增进展",
        "current_scene": {"location": "30字内", "time_anchor": "30字内", "activity": "30字内"},
        "events": [{"text": "80字内；最多6项", "evidence_ids": ["消息ID"]}],
        "open_threads": [{"text": "80字内；最多4项", "evidence_ids": ["消息ID"]}],
        "commitments": [{"text": "80字内；最多4项", "evidence_ids": ["消息ID"]}],
        "relationship_deltas": [{"text": "80字内；最多4项", "evidence_ids": ["消息ID"]}],
        "confirmed_facts": [{"text": "80字内；最多6项", "evidence_ids": ["消息ID"]}],
        "adult_facts": [{"text": "80字内；最多6项；仅ADULT段", "evidence_ids": ["消息ID"]}],
        "temporary_cues": [{"text": "80字内；最多3项", "evidence_ids": ["消息ID"]}],
        "lane": "DAILY|ROMANCE|ADULT",
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


def _evidenced_items(
    value: Any,
    *,
    allowed_ids: set[str],
    evidence_roles: dict[str, str],
    required_role: str,
    limit: int,
    lane: str,
    allow_adult_detail: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        evidence_ids = [
            str(identifier)
            for identifier in item.get("evidence_ids", [])
            if str(identifier) in allowed_ids
        ]
        if not text or not evidence_ids:
            continue
        # A model-authored reply cannot establish a user action, quote, shared
        # event or relationship fact on its own. Require user provenance for
        # continuity claims; only the character's own commitments are allowed
        # to rely solely on an assistant message.
        if required_role not in {evidence_roles.get(identifier) for identifier in evidence_ids}:
            continue
        text = text.replace("她", "TA")
        # The shared continuity packet is visible again when adult mode is off.
        # Explicit anatomy/actions therefore never enter it, even if a provider
        # mislabels a mixed segment as DAILY or ROMANCE.
        if _ADULT_DETAIL.search(text) and not allow_adult_detail:
            continue
        result.append(
            {
                "text": text[:600],
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
                "lane": lane,
            }
        )
        if len(result) >= limit:
            break
    return result


def _merge_items(
    previous: Any,
    current: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_text: dict[str, int] = {}
    for source in (previous if isinstance(previous, list) else [], current):
        for item in source:
            if not isinstance(item, dict):
                continue
            text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            if not text:
                continue
            key = text.casefold()
            normalized = {
                "text": text,
                "evidence_ids": list(dict.fromkeys(str(v) for v in item.get("evidence_ids", []))),
                "lane": str(item.get("lane") or "DAILY"),
            }
            if key in by_text:
                merged[by_text[key]] = normalized
            else:
                by_text[key] = len(merged)
                merged.append(normalized)
    return merged[-limit:]


def parse_compaction_output(raw: str, payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("compaction response did not contain a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("compaction response must be an object")
    allowed_ids = set(str(item) for item in payload.get("source", {}).get("message_ids", []))
    if not allowed_ids:
        raise ValueError("compaction input has no source message IDs")
    evidence_roles = {
        str(item.get("message_id")): str(item.get("role") or "")
        for item in payload.get("dialogue", [])
        if isinstance(item, dict) and str(item.get("message_id") or "").strip()
    }
    previous = payload.get("previous_summary")
    previous = previous if isinstance(previous, dict) else {}
    source_lanes = [
        str(item.get("companion_lane") or "DAILY")
        for item in payload.get("dialogue", [])
        if isinstance(item, dict)
        and str(item.get("companion_lane") or "DAILY") in {"DAILY", "ROMANCE", "ADULT"}
    ]
    # Lane classification is server-owned. The model may describe the segment,
    # but it cannot promote or demote an adult turn by changing this field.
    lane = source_lanes[-1] if source_lanes else str(previous.get("lane") or "DAILY")
    if lane not in {"DAILY", "ROMANCE", "ADULT"}:
        lane = "DAILY"
    delta_summary = (
        re.sub(r"\s+", " ", str(value.get("dialogue_summary") or "")).strip().replace("她", "TA")
    )
    if not delta_summary:
        raise ValueError("compaction dialogue_summary is blank")
    if _ADULT_DETAIL.search(delta_summary):
        delta_summary = "双方在成人模式中发生了明确同意的亲密互动；露骨细节未进入通用连续性包。"
    previous_overview = str(previous.get("continuity_overview") or "").strip()
    continuity_overview = "；".join(item for item in (previous_overview, delta_summary) if item)[
        -3000:
    ]
    source = dict(payload.get("source") or {})
    source["cutoff_sequence"] = int(payload["cutoff_sequence"])
    source["lanes"] = list(dict.fromkeys(source_lanes))
    source["lane"] = lane
    segments = [item for item in previous.get("source_segments", []) if isinstance(item, dict)]
    segments.append(source)
    current_scene = previous.get("current_scene", {})
    raw_scene = value.get("current_scene")
    if isinstance(raw_scene, dict):
        current_scene = {
            key: str(raw_scene.get(key) or current_scene.get(key) or "").strip()[:300]
            for key in ("location", "time_anchor", "activity")
        }
    if lane == "ADULT":
        current_scene = previous.get("current_scene", {})
    lane_overviews = dict(previous.get("lane_overviews") or {})
    lane_overviews[lane] = "；".join(
        item for item in (str(lane_overviews.get(lane) or "").strip(), delta_summary) if item
    )[-1800:]
    result: dict[str, Any] = {
        "summary_version": 2,
        "cutoff_sequence": int(payload["cutoff_sequence"]),
        "continuity_overview": continuity_overview,
        "current_scene": current_scene,
        "source_segments": segments[-24:],
        "lane": lane,
        "lane_overviews": lane_overviews,
    }
    limits = {
        "recent_events": 16,
        "open_threads": 10,
        "commitments": 12,
        "relationship_deltas": 12,
        "confirmed_facts": 20,
        "temporary_cues": 8,
        "adult_facts": 24,
    }
    input_keys = {
        "recent_events": "events",
        "open_threads": "open_threads",
        "commitments": "commitments",
        "relationship_deltas": "relationship_deltas",
        "confirmed_facts": "confirmed_facts",
        "temporary_cues": "temporary_cues",
        "adult_facts": "adult_facts",
    }
    for output_key, input_key in input_keys.items():
        delta_items = _evidenced_items(
            value.get(input_key),
            allowed_ids=allowed_ids,
            evidence_roles=evidence_roles,
            required_role="assistant" if output_key == "commitments" else "user",
            limit=limits[output_key],
            lane=lane,
            allow_adult_detail=bool(output_key == "adult_facts" and lane == "ADULT"),
        )
        if output_key == "adult_facts" and lane != "ADULT":
            delta_items = []
        result[output_key] = _merge_items(
            previous.get(output_key), delta_items, limit=limits[output_key]
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
        character_for_session: Callable[[str], str] | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.profiles = profiles
        self.llm_provider = llm_provider
        self.active_run_count = active_run_count
        self.character_for_session = character_for_session or (lambda _session_id: "")
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
            summary = parse_compaction_output(raw, payload)
            current_profiles: ProfileBundle = self.profiles.load_bundle(
                self.character_for_session(job.session_id)
            )
            self.ledger.activate_compaction(job, summary=summary, profiles=current_profiles)
        except Exception as exc:  # noqa: BLE001 - durable retry owns failure semantics
            self.ledger.fail_compaction(job.job_id, str(exc), retry=True)
