"""Small, mutable event memory between live context and long-term retrieval."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
import re
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.models import ApiConfig, ChatRequest, ChatResponse
from mindspace_graph.product_database import ProductDatabase


PENDING_LIMIT = 3
SUBJECT_CATEGORIES = ("user_related", "ai_related", "relationship_related")
_GROUPS = {"pending", "subject"}
_CATEGORY_ALIASES = {
    "user": "user_related", "用户": "user_related", "用户相关": "user_related",
    "ai": "ai_related", "agent": "ai_related", "角色": "ai_related",
    "ai相关": "ai_related", "角色相关": "ai_related",
    "relationship": "relationship_related", "关系": "relationship_related",
    "关系相关": "relationship_related",
}
_GROUP_ALIASES = {
    "todo": "pending", "recent": "pending", "近期": "pending", "待办": "pending",
    "近期/待办": "pending", "dynamic": "subject", "主体": "subject", "动态": "subject",
}
_EVENT_CANDIDATE = re.compile(
    r"(?:记住|记一下|提醒|别忘|到时候|待办|约定|约好|说好|答应|承诺|改期|取消|完成|"
    r"改到|改成|推迟|提前|时间改|"
    r"明天|后天|周末|下周|下个月|今晚|稍后|待会|届时|"
    r"准备|计划|打算|决定|报名|考试|面试|出差|旅行|搬家|入职|离职|辞职|"
    r"生病|住院|手术|吵架|和好|复合|分手|订婚|结婚|纪念日|"
    r"最近|刚刚|刚才|今天发生|我们(?:要|会|一起|已经))",
    re.IGNORECASE,
)
_EXTERNAL_REQUEST = re.compile(
    r"(?:联网|上网|网络搜索|搜索|帮我查|查一下|查查|查找|查询|打开|GitHub|"
    r"天气|新闻|最新|实时|股价|汇率|比分|赛程|航班|路况|"
    r"推文|帖子|动态|社交平台|OpenAI|Twitter|X\.com|小红书|微博)",
    re.IGNORECASE,
)
_MATCH_STOP_GRAMS = {
    "用户", "事情", "已经", "这是", "今天", "刚刚", "正式", "当前", "相关", "记住",
    "提醒", "约定", "取消", "完成", "开始", "结果", "之后", "一起",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"</?(?:T|R):[^>]*>", "", text, flags=re.IGNORECASE)
    return text[:limit]


def should_consider_event(message: str) -> bool:
    """Cheap gate only; the private model remains the semantic judge."""
    text = re.sub(r"\s+", "", message or "")
    if len(text) < 2 or text in {"好的", "知道了", "可以", "行吧", "嗯嗯"}:
        return False
    return bool(_EVENT_CANDIDATE.search(text))


def event_memory_lane(message: str) -> bool:
    """Event writes are exclusive, while explicit external-information requests win."""
    from mindspace_graph.web.routing import infer_platforms

    return should_consider_event(message) and not bool(
        _EXTERNAL_REQUEST.search(message or "") or infer_platforms(message or "")
    )


def parse_event_operation(raw: str) -> dict[str, Any]:
    """Accept compact JSON while tolerating fences and harmless wrappers."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        value = None
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                value, _ = decoder.raw_decode(text[match.start():])
                break
            except json.JSONDecodeError:
                continue
        if value is None:
            raise ValueError("event memory extractor did not return JSON") from exc
    if isinstance(value, dict) and isinstance(value.get("event_memory"), dict):
        value = value["event_memory"]
    if isinstance(value, dict) and isinstance(value.get("operations"), list):
        value = next((item for item in value["operations"] if isinstance(item, dict)), {"operation": "none"})
    if not isinstance(value, dict):
        raise ValueError("event memory operation must be an object")
    nested = value.get("event")
    if isinstance(nested, dict):
        value = {**nested, **{key: item for key, item in value.items() if key != "event"}}
    operation = str(value.get("operation") or value.get("op") or "none").strip().lower()
    if operation not in {"none", "add", "update", "complete", "remove"}:
        raise ValueError("unsupported event memory operation")
    if operation == "none":
        return {"operation": "none"}
    group = _GROUP_ALIASES.get(str(value.get("group") or "subject").strip().lower(), str(value.get("group") or "subject").strip().lower())
    category = _CATEGORY_ALIASES.get(str(value.get("category") or "relationship_related").strip().lower(), str(value.get("category") or "relationship_related").strip().lower())
    if group not in _GROUPS or category not in SUBJECT_CATEGORIES:
        raise ValueError("invalid event memory group or category")
    normalized = {
        "operation": operation,
        "group": group,
        "category": category,
        "target_id": _clean_text(value.get("target_id") or value.get("id"), 80),
        "title": _clean_text(value.get("title"), 20),
        "summary": _clean_text(value.get("summary") or value.get("content"), 160),
        "due_at": _clean_text(value.get("due_at"), 64) or None,
        "importance": max(1, min(3, int(value.get("importance") or 2))),
    }
    if operation in {"add", "update"} and not (normalized["title"] and normalized["summary"]):
        raise ValueError("event title and summary are required")
    if operation in {"complete", "remove"} and not normalized["target_id"]:
        raise ValueError("target_id is required")
    return normalized


def build_event_extraction_messages(request: ChatRequest, response: ChatResponse, snapshot: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "physical_time": request.client_sent_at.isoformat() if request.client_sent_at else _now(),
        "current_user": request.message,
        "current_response": response.reply,
        "existing_events": {"pending": snapshot.get("pending", []), "subjects": snapshot.get("subjects", {})},
    }
    rules = """你是事件记忆判断器，只返回一个 JSON 对象，不要解释。
中期记忆只保存具体事件，不保存偏好、身份、性格、普通寒暄、临时动作、色情过程或泛化总结。
pending 只保存尚未完成、未来可以明确完成或取消的单次事项、提醒和预约。
subject 保存已经发生或已经成立的重要事件；AI 已作出的决定进入 subject/ai_related，关系成立或变化进入 subject/relationship_related。
category 必须是 user_related、ai_related、relationship_related。
只有用户明确陈述或确认的事实才可写入。current_response 只能作为 AI 自己作出的具体承诺或双方已明确达成约定的证据，不能把角色即兴发挥写成事实。
疑问、假设、玩笑、建议、联网或工具结果、用户未确认的模型推测都返回 none。
同一事件有进展用 update；完成用 complete；取消或明确要求忘记用 remove；不要重复 add。
update、complete、remove 的 target_id 必须与当前用户原话直接提到的事件标题或内容相符，不要仅凭事件顺序猜测。
每轮最多一个操作。title 是不超过20字的直接标题，summary 不超过160字。
返回格式：
{"operation":"none"}
或
{"operation":"add|update|complete|remove","group":"pending|subject","category":"user_related|ai_related|relationship_related","target_id":"","title":"","summary":"","due_at":null,"importance":1}"""
    return [
        {"role": "system", "content": rules},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def _match_grams(value: Any) -> set[str]:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()
    return {text[index:index + 2] for index in range(max(0, len(text) - 1))} - _MATCH_STOP_GRAMS


def _target_score(message: str, event: dict[str, Any]) -> int:
    source = _match_grams(message)
    title_hits = len(source & _match_grams(event.get("title")))
    summary_hits = len(source & _match_grams(event.get("summary")))
    return title_hits * 3 + summary_hits


def resolve_event_target(operation: dict[str, Any], snapshot: dict[str, Any], message: str) -> dict[str, Any]:
    """Correct or reject destructive model-selected IDs using current-user evidence."""
    if operation.get("operation") not in {"update", "complete", "remove"}:
        return operation
    active = [item for item in snapshot.get("pending", []) if isinstance(item, dict)]
    active.extend(item for item in snapshot.get("subjects", {}).values() if isinstance(item, dict))
    if not active:
        raise ValueError("no active event memory target")
    selected_id = str(operation.get("target_id") or "")
    selected = next((item for item in active if item.get("id") == selected_id), None)
    scored = sorted(((_target_score(message, item), item) for item in active), key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    selected_score = _target_score(message, selected) if selected is not None else 0
    if best_score < 3:
        if selected is not None and len(active) == 1:
            return operation
        raise ValueError("event target is not supported by current-user wording")
    if selected is None or best.get("id") != selected_id and best_score > selected_score:
        return {**operation, "target_id": str(best.get("id") or ""), "_target_corrected": True}
    return operation


def normalize_event_operation(operation: dict[str, Any], message: str) -> dict[str, Any]:
    """Make slot and lifecycle semantics deterministic after model extraction."""
    normalized = dict(operation)
    action = str(normalized.get("operation") or "none")
    if action == "add" and normalized.get("category") == "ai_related" and not normalized.get("due_at"):
        normalized["group"] = "subject"
    if action in {"update", "complete", "remove"}:
        text = re.sub(r"\s+", "", message or "")
        # A reschedule commonly contains cancellation words for the old time
        # (for example, "改到后天，明晚不用提醒"). Treat the whole utterance
        # as an update before considering destructive lifecycle operations.
        if re.search(r"(?:改到|改成|改为|推迟|提前|时间改|原来的.{0,16}(?:不要|取消|不用|不算))", text):
            normalized["operation"] = "update"
        elif re.search(r"(?:已经.{0,12}(?:完成|做完|交完|结束|办完)|(?:完成|做完|交完|办完)了)", text):
            normalized["operation"] = "complete"
        elif re.search(r"(?:取消|作废|不(?:看|去|做|要)了|不用提醒|别提醒|忘掉)", text):
            normalized["operation"] = "remove"
    return normalized


class EventMemoryStore:
    """Per-character six-slot event document with deterministic replacement."""
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database
        self._lock = RLock()

    @staticmethod
    def _key(character_id: str) -> str:
        return f"event_memory:{character_id or '__default__'}"

    @staticmethod
    def _empty(character_id: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0", "character_id": character_id, "revision": 0,
            "pending": [], "subjects": {category: None for category in SUBJECT_CATEGORIES},
            "history": [], "updated_at": _now(),
        }

    def snapshot(self, character_id: str) -> dict[str, Any]:
        with self._lock:
            value = self.database.get_document(self._key(character_id), self._empty(character_id))
            if not isinstance(value, dict):
                value = self._empty(character_id)
            value.setdefault("pending", [])
            subjects = value.setdefault("subjects", {})
            for category in SUBJECT_CATEGORIES:
                subjects.setdefault(category, None)
            value.setdefault("history", [])
            return deepcopy(value)

    def prompt_snapshot(self, character_id: str) -> dict[str, Any]:
        value = self.snapshot(character_id)
        subjects = {key: item for key, item in value["subjects"].items() if isinstance(item, dict)}
        return {
            "revision": int(value.get("revision", 0)), "pending": list(value.get("pending", []))[:PENDING_LIMIT],
            "subjects": subjects, "active_count": len(value.get("pending", [])) + len(subjects),
        }

    @staticmethod
    def _find(value: dict[str, Any], event_id: str) -> tuple[str, str | int, dict[str, Any]] | None:
        for index, event in enumerate(value.get("pending", [])):
            if isinstance(event, dict) and event.get("id") == event_id:
                return "pending", index, event
        for category, event in value.get("subjects", {}).items():
            if isinstance(event, dict) and event.get("id") == event_id:
                return "subject", category, event
        return None

    @staticmethod
    def _archive(value: dict[str, Any], event: dict[str, Any], status: str) -> None:
        value["history"] = [{**event, "status": status, "updated_at": _now()}, *value.get("history", [])][:30]

    def apply(self, character_id: str, operation: dict[str, Any], *, session_id: str = "", source_message_ids: list[str] | None = None) -> dict[str, Any]:
        action = str(operation.get("operation") or "none")
        if action == "none":
            return {"changed": False, "operation": "none", "snapshot": self.snapshot(character_id)}
        with self._lock, self.database.transaction(operation="event_memory_write", details={"character_id": character_id, "action": action}):
            value = self.snapshot(character_id)
            now = _now()
            changed_event: dict[str, Any] | None = None
            if action == "add":
                event = {
                    "id": f"evt_{uuid4().hex}", "group": operation["group"], "category": operation["category"],
                    "title": _clean_text(operation["title"], 20), "summary": _clean_text(operation["summary"], 160),
                    "status": "active", "due_at": operation.get("due_at"),
                    "importance": max(1, min(3, int(operation.get("importance") or 2))),
                    "source_session_id": session_id, "source_message_ids": list(source_message_ids or [])[:4],
                    "created_at": now, "updated_at": now,
                }
                if event["group"] == "pending":
                    duplicate = next((item for item in value["pending"] if item.get("title") == event["title"]), None)
                    if duplicate is not None:
                        duplicate.update({key: item for key, item in event.items() if key not in {"id", "created_at"}})
                        duplicate["updated_at"] = now
                        changed_event, action = duplicate, "update"
                    else:
                        if len(value["pending"]) >= PENDING_LIMIT:
                            victim = min(value["pending"], key=lambda item: (int(item.get("importance") or 1), str(item.get("updated_at") or "")))
                            value["pending"].remove(victim)
                            self._archive(value, victim, "replaced")
                        value["pending"].append(event)
                        changed_event = event
                else:
                    previous = value["subjects"].get(event["category"])
                    if isinstance(previous, dict):
                        self._archive(value, previous, "replaced")
                    value["subjects"][event["category"]] = event
                    changed_event = event
            else:
                located = self._find(value, str(operation.get("target_id") or ""))
                if located is None:
                    raise KeyError("event memory target not found")
                group, location, event = located
                if action == "update":
                    event.update({
                        "title": _clean_text(operation["title"], 20), "summary": _clean_text(operation["summary"], 160),
                        "due_at": operation.get("due_at"), "importance": max(1, min(3, int(operation.get("importance") or 2))),
                        "updated_at": now, "source_session_id": session_id or event.get("source_session_id", ""),
                        "source_message_ids": list(source_message_ids or event.get("source_message_ids", []))[:4],
                    })
                    changed_event = event
                else:
                    status = "completed" if action == "complete" else "cancelled"
                    if group == "pending":
                        value["pending"].pop(int(location))
                    else:
                        value["subjects"][str(location)] = None
                    self._archive(value, event, status)
                    changed_event = {**event, "status": status, "updated_at": now}
            value["revision"] = int(value.get("revision", 0)) + 1
            value["updated_at"] = now
            self.database.put_document(self._key(character_id), value)
            return {"changed": True, "operation": action, "event": deepcopy(changed_event), "snapshot": deepcopy(value)}


class EventMemoryWritebackService:
    """Post-response event extraction that never competes with a tool turn."""
    def __init__(self, *, dependencies: Any, store: EventMemoryStore, api_provider: Any) -> None:
        self.dependencies, self.store, self.api_provider = dependencies, store, api_provider
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def kick(self, request: ChatRequest, response: ChatResponse) -> None:
        if (response.status != "success" or request.mode != "primary" or request.initiative
            or response.tool_execution is not None or not event_memory_lane(request.message)
                or not callable(getattr(self.dependencies.llm, "extract_memory", None))):
            return
        key = f"{request.session_id}:{request.round}"
        if key in self._tasks and not self._tasks[key].done():
            return
        self._tasks[key] = asyncio.create_task(self._run(key, request, response), name=f"event-memory-{request.session_id[:10]}-{request.round}")

    async def flush_session(self, session_id: str) -> None:
        tasks = [task for key, task in self._tasks.items() if key.startswith(f"{session_id}:") and not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

    def _message_ids(self, request: ChatRequest, response: ChatResponse) -> list[str]:
        ids: list[str] = []
        for item in reversed(self.dependencies.sessions.load_all(request.session_id)):
            if int(item.get("round") or 0) != request.round:
                continue
            identifier = str(item.get("message_id") or item.get("id") or "")
            if identifier and identifier not in ids:
                ids.append(identifier)
        if response.assistant_message_id and response.assistant_message_id not in ids:
            ids.append(response.assistant_message_id)
        return ids[:4]

    async def _run(self, key: str, request: ChatRequest, response: ChatResponse) -> None:
        lock = self._locks.setdefault(request.character_id or "__default__", asyncio.Lock())
        try:
            async with lock:
                snapshot = self.store.snapshot(request.character_id)
                raw = await asyncio.to_thread(self.dependencies.llm.extract_memory, build_event_extraction_messages(request, response, snapshot), self.api_provider(), timeout_seconds=8.0)
                operation = normalize_event_operation(parse_event_operation(raw), request.message)
                operation = resolve_event_target(operation, snapshot, request.message)
                result = self.store.apply(request.character_id, operation, session_id=request.session_id, source_message_ids=self._message_ids(request, response))
                self.dependencies.audit.record(
                    "event_memory_completed" if result["changed"] else "event_memory_skipped",
                    {"session_id": request.session_id, "character_id": request.character_id, "round": request.round,
                     "operation": result["operation"], "event_id": str((result.get("event") or {}).get("id") or ""),
                     "target_corrected": bool(operation.get("_target_corrected"))},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.dependencies.audit.record("event_memory_failed", {"session_id": request.session_id, "character_id": request.character_id, "round": request.round, "error": str(exc)[:500]})
        finally:
            self._tasks.pop(key, None)
