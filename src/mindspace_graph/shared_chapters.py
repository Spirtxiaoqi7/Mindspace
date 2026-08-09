"""Shared-chapter entertainment data without weakening profile authority.

The feature deliberately keeps journals, relationship moments and activity
sessions outside ``runtime_state``.  They are product records with their own
revision and lifecycle, not facts that the model may silently write into a
character profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from mindspace_graph.models import ApiConfig, RetrievedChunk
from mindspace_graph.product_database import ProductDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


class JournalCreate(BaseModel):
    title: str = Field(default="", max_length=120)
    content: str = Field(default="", max_length=12_000)
    status: Literal["draft", "saved", "archived"] = "draft"
    source: Literal["user_written", "assistant_draft", "activity_summary", "template"] = "user_written"
    session_id: str = Field(default="", max_length=100)
    activity_session_id: str = Field(default="", max_length=100)
    cover_asset_id: str = Field(default="journal-cover-paper", max_length=120)
    source_round_start: int | None = Field(default=None, ge=0)
    source_round_end: int | None = Field(default=None, ge=0)
    source_message_count: int = Field(default=0, ge=0, le=64)

    @field_validator("title", "content", "session_id", "activity_session_id")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class JournalUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=120)
    content: str | None = Field(default=None, max_length=12_000)
    status: Literal["draft", "saved", "archived"] | None = None
    cover_asset_id: str | None = Field(default=None, max_length=120)


class MomentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2_000)
    event_type: str = Field(default="shared_moment", max_length=64)
    status: Literal["candidate", "saved", "archived"] = "candidate"
    source: Literal["user_confirmed", "activity_completion", "journal", "profile_migration"] = "user_confirmed"
    session_id: str = Field(default="", max_length=100)
    activity_session_id: str = Field(default="", max_length=100)
    journal_entry_id: str = Field(default="", max_length=100)
    art_asset_id: str = Field(default="moment-heart-keepsake", max_length=120)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class MomentUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=2_000)
    status: Literal["candidate", "saved", "archived"] | None = None
    art_asset_id: str | None = Field(default=None, max_length=120)


class ActivityStart(BaseModel):
    character_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(default="", max_length=100)


class ActivityAction(BaseModel):
    action_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    action: Literal[
        "select_scene",
        "draw_question",
        "answer_question",
        "choose_story",
        "complete",
        "cancel",
        "resume",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class SceneSelectionUpdate(BaseModel):
    scene_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(default=0, ge=0)


ACTIVITY_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "activity_id": "scene_companion",
        "title": "场景同行",
        "description": "选一个当下场景，让对话围绕正在共同经历的片刻持续发展。",
        "icon_asset_id": "activity-scene",
        "cover_asset_id": "scene-riverside",
        "initial_phase": "choose_scene",
        "scenes": [
            {
                "scene_id": "riverside_evening",
                "title": "河畔晚风",
                "description": "暮色刚落，沿着亮起路灯的河岸慢慢走。",
                "asset_id": "scene-riverside",
            },
            {
                "scene_id": "rainy_living_room",
                "title": "雨夜客厅",
                "description": "雨声落在窗上，桌边留着两杯还温热的饮品。",
                "asset_id": "scene-rainy-room",
            },
            {
                "scene_id": "spring_park",
                "title": "春日公园",
                "description": "雨后的晨光落在小路上，慢慢走过还带水汽的树影。",
                "asset_id": "scene-spring-park",
            },
            {
                "scene_id": "sunset_rooftop",
                "title": "落日天台",
                "description": "并排坐在城市上空，看晚霞一点点沉下去。",
                "asset_id": "scene-sunset-rooftop",
            },
            {
                "scene_id": "night_market",
                "title": "夜市闲逛",
                "description": "灯火和食物香气挤在街巷里，边走边挑想分享的味道。",
                "asset_id": "scene-night-market",
            },
            {
                "scene_id": "library_afternoon",
                "title": "图书馆午后",
                "description": "各自翻着一本书，偶尔把喜欢的段落递给对方。",
                "asset_id": "scene-library-afternoon",
            },
            {
                "scene_id": "seaside_dawn",
                "title": "海边清晨",
                "description": "潮声还很轻，沿着空旷的海岸等第一束日光。",
                "asset_id": "scene-seaside-dawn",
            },
            {
                "scene_id": "mountain_cabin",
                "title": "山间木屋",
                "description": "火炉刚暖起来，窗外是安静的松林和薄雾。",
                "asset_id": "scene-mountain-cabin",
            },
            {
                "scene_id": "snowy_window",
                "title": "雪夜窗边",
                "description": "隔着热茶的雾气，看雪慢慢盖住屋檐。",
                "asset_id": "scene-snowy-window",
            },
            {
                "scene_id": "summer_balcony",
                "title": "夏夜阳台",
                "description": "灯串轻轻亮着，晚风里只剩远处的虫鸣。",
                "asset_id": "scene-summer-balcony",
            },
            {
                "scene_id": "autumn_train",
                "title": "秋日列车",
                "description": "车窗外的山谷不断后退，桌上放着两张车票。",
                "asset_id": "scene-autumn-train",
            },
            {
                "scene_id": "cafe_corner",
                "title": "咖啡馆角落",
                "description": "阴天的窗边很安静，甜点只吃到一半。",
                "asset_id": "scene-cafe-corner",
            },
            {
                "scene_id": "kitchen_evening",
                "title": "晚饭后的厨房",
                "description": "料理刚收尾，桌面还留着一点面粉和热气。",
                "asset_id": "scene-kitchen-evening",
            },
            {
                "scene_id": "stargazing_field",
                "title": "原野观星",
                "description": "铺好毯子和小灯，在风里找属于今晚的星星。",
                "asset_id": "scene-stargazing-field",
            },
            {
                "scene_id": "museum_hall",
                "title": "美术馆长廊",
                "description": "在安静的展厅里停停走走，各自说出第一眼的感受。",
                "asset_id": "scene-museum-hall",
            },
            {
                "scene_id": "flower_shop",
                "title": "春日花店",
                "description": "花纸和丝带铺满柜台，一起挑一枝想留下的花。",
                "asset_id": "scene-flower-shop",
            },
            {
                "scene_id": "old_street",
                "title": "雨后旧街",
                "description": "石板还泛着水光，沿着亮灯的老房子慢慢往前。",
                "asset_id": "scene-old-street",
            },
            {
                "scene_id": "lakeside_picnic",
                "title": "湖畔野餐",
                "description": "风吹过水面，毯子上只摆着简单的水果和饮品。",
                "asset_id": "scene-lakeside-picnic",
            },
            {
                "scene_id": "festival_lantern",
                "title": "夏祭灯火",
                "description": "在人群与灯笼之间并肩走着，偶尔停下来等对方。",
                "asset_id": "scene-festival-lantern",
            },
            {
                "scene_id": "late_night_drive",
                "title": "深夜兜风",
                "description": "雨水划过车窗，城市灯光从挡风玻璃上缓缓掠过。",
                "asset_id": "scene-late-night-drive",
            },
        ],
    },
    {
        "activity_id": "mutual_questions",
        "title": "默契问答",
        "description": "从轻松问题开始，不把回答当成测试，也不计算对错。",
        "icon_asset_id": "activity-questions",
        "cover_asset_id": "state-journal-generating",
        "initial_phase": "ready",
        "questions": [
            "最近哪一件小事让你觉得今天还不错？",
            "如果现在能一起出门，你更想去安静的地方还是热闹的地方？",
            "有什么看似普通、但你会一直记得的瞬间？",
            "你希望对方在你沉默的时候怎么陪着你？",
            "最近有没有一个想一起完成、但还没开始的计划？",
            "哪一种天气最适合两个人慢慢聊天？",
        ],
    },
    {
        "activity_id": "story_choices",
        "title": "片刻故事",
        "description": "通过本地分支卡推进一段小故事，模型只负责角色当下的表达。",
        "icon_asset_id": "activity-story",
        "cover_asset_id": "state-moment-saved",
        "initial_phase": "node:arrival",
        "nodes": {
            "arrival": {
                "text": "临时停电后，房间里只剩窗外的微光。",
                "choices": [
                    {"choice_id": "find_candle", "label": "一起找蜡烛", "next": "candle"},
                    {"choice_id": "sit_window", "label": "先坐到窗边", "next": "window"},
                ],
            },
            "candle": {
                "text": "烛光亮起来，桌上的影子轻轻晃动。",
                "choices": [{"choice_id": "share_story", "label": "讲一件旧事", "next": "ending"}],
            },
            "window": {
                "text": "远处楼群也暗了下来，城市忽然变得很安静。",
                "choices": [
                    {
                        "choice_id": "listen_rain",
                        "label": "一起听一会儿",
                        "next": "ending",
                    }
                ],
            },
            "ending": {"text": "电还没有来，但这一小段等待已经有了自己的意义。", "choices": []},
        },
    },
)

# Scene companion started as an activity in 0.7.0. Keep its definition readable
# for old audit records, but expose its artwork through a lightweight
# conversation-scene catalog instead of the activity state machine.
LEGACY_SCENE_ACTIVITY = next(item for item in ACTIVITY_DEFINITIONS if item["activity_id"] == "scene_companion")
SCENE_LOCATION_LABELS: dict[str, str] = {
    "riverside_evening": "暮色中的河岸边",
    "rainy_living_room": "下着雨的客厅窗边",
    "spring_park": "雨后清晨的公园小路上",
    "sunset_rooftop": "能看见城市晚霞的天台上",
    "night_market": "灯火热闹的夜市街巷里",
    "library_afternoon": "安静的图书馆窗边",
    "seaside_dawn": "清晨空旷的海岸边",
    "mountain_cabin": "有火炉和薄雾的山间木屋里",
    "snowy_window": "飘雪夜晚的窗边",
    "summer_balcony": "有灯串和晚风的夏夜阳台上",
    "autumn_train": "穿过秋日山谷的列车里",
    "cafe_corner": "阴天安静的咖啡馆角落",
    "kitchen_evening": "刚做完晚饭的厨房里",
    "stargazing_field": "铺着毯子的原野星空下",
    "museum_hall": "安静的美术馆长廊里",
    "flower_shop": "摆满鲜花和丝带的花店里",
    "old_street": "雨后亮起灯的旧街上",
    "lakeside_picnic": "微风吹过的湖畔野餐地",
    "festival_lantern": "挂满灯笼的夏祭人群中",
    "late_night_drive": "雨夜缓慢行驶的车里",
}
SCENE_DEFINITIONS: tuple[dict[str, Any], ...] = tuple(
    {
        **deepcopy(scene),
        "location": SCENE_LOCATION_LABELS[str(scene["scene_id"])],
    }
    for scene in LEGACY_SCENE_ACTIVITY["scenes"]
)
CUSTOM_SCENE_PREFIX = "custom-scene:"
ACTIVITY_DEFINITIONS = tuple(item for item in ACTIVITY_DEFINITIONS if item["activity_id"] != "scene_companion")


class SharedChapterService:
    """Transactional repositories for journals, moments and deterministic activities."""

    def __init__(
        self,
        database: ProductDatabase,
        *,
        characters: Any,
        sessions: Any,
        audit: Any,
        llm_provider: Callable[[], Any] | None = None,
        api_provider: Callable[[], ApiConfig] | None = None,
    ) -> None:
        self.database = database
        self.characters = characters
        self.sessions = sessions
        self.audit = audit
        self.llm_provider = llm_provider
        self.api_provider = api_provider
        self._migrate_confirmed_continuity()

    @staticmethod
    def _journal_key(character_id: str, entry_id: str) -> str:
        return f"journal:{character_id}:{entry_id}"

    @staticmethod
    def _moment_key(character_id: str, moment_id: str) -> str:
        return f"moment:{character_id}:{moment_id}"

    @staticmethod
    def _activity_key(activity_session_id: str) -> str:
        return f"activity-session:{activity_session_id}"

    @staticmethod
    def _scene_key(session_id: str) -> str:
        return f"conversation-scene:{session_id}"

    @staticmethod
    def _character_scene_key(character_id: str) -> str:
        return f"character-scene-default:{character_id}"

    @staticmethod
    def _custom_scene_key(scene_id: str) -> str:
        return f"{CUSTOM_SCENE_PREFIX}{scene_id}"

    def _custom_scenes(self) -> list[dict[str, Any]]:
        return [
            deepcopy(value)
            for _key, value in self.database.list_documents(CUSTOM_SCENE_PREFIX)
            if isinstance(value, dict)
        ]

    def _find_scene(self, scene_id: str) -> dict[str, Any] | None:
        built_in = next((item for item in SCENE_DEFINITIONS if item["scene_id"] == scene_id), None)
        if built_in is not None:
            return deepcopy(built_in)
        custom = self.database.get_document(self._custom_scene_key(scene_id))
        return deepcopy(custom) if isinstance(custom, dict) else None

    def _character(self, character_id: str) -> dict[str, Any]:
        record = self.characters.get(character_id)
        if record.get("status") != "active":
            raise ValueError("selected character is archived")
        return record

    def summary(self, character_id: str) -> dict[str, Any]:
        self._character(character_id)
        journals = self.list_journals(character_id, include_archived=True)
        moments = self.list_moments(character_id, include_archived=True)
        sessions = [
            value
            for _key, value in self.database.list_documents("activity-session:")
            if isinstance(value, dict) and value.get("character_id") == character_id
        ]
        saved_count = sum(item.get("status") == "saved" for item in moments)
        thresholds = (0, 3, 8, 16, 30)
        heart_index = max(index for index, threshold in enumerate(thresholds) if saved_count >= threshold)
        return {
            "character_id": character_id,
            "journal_count": sum(item.get("status") != "archived" for item in journals),
            "moment_count": saved_count,
            "candidate_moment_count": sum(item.get("status") == "candidate" for item in moments),
            "activity_count": sum(item.get("status") == "completed" for item in sessions),
            "heart_state": ("empty", "trace", "warm", "glow", "keepsake")[heart_index],
            "next_heart_at": thresholds[heart_index + 1] if heart_index < 4 else None,
        }

    def list_journals(self, character_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self._character(character_id)
        items = [
            deepcopy(value)
            for _key, value in self.database.list_documents(f"journal:{character_id}:")
            if isinstance(value, dict)
        ]
        if not include_archived:
            items = [item for item in items if item.get("status") != "archived"]
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def create_journal(self, character_id: str, payload: JournalCreate) -> dict[str, Any]:
        self._character(character_id)
        now = _now()
        entry = {
            "entry_id": uuid4().hex,
            "character_id": character_id,
            "revision": 1,
            "title": _clean_text(payload.title, 120) or "未命名小记",
            "content": payload.content.strip()[:12_000],
            "status": payload.status,
            "source": payload.source,
            "session_id": payload.session_id,
            "activity_session_id": payload.activity_session_id,
            "cover_asset_id": payload.cover_asset_id,
            "source_round_start": payload.source_round_start,
            "source_round_end": payload.source_round_end,
            "source_message_count": payload.source_message_count,
            "visibility": "narrative_only",
            "eligible_for_json_evidence": False,
            "created_at": now,
            "updated_at": now,
        }
        with self.database.transaction(operation="create_journal", details={"character_id": character_id}):
            self.database.put_document(self._journal_key(character_id, entry["entry_id"]), entry)
        self.audit.record(
            "journal_created",
            {
                "character_id": character_id,
                "entry_id": entry["entry_id"],
                "source": entry["source"],
            },
        )
        return deepcopy(entry)

    def update_journal(self, character_id: str, entry_id: str, payload: JournalUpdate) -> dict[str, Any]:
        key = self._journal_key(character_id, entry_id)
        current = self.database.get_document(key)
        if not isinstance(current, dict):
            raise KeyError("journal entry not found")
        if int(current.get("revision", 0)) != payload.expected_revision:
            raise ValueError("journal revision conflict")
        candidate = deepcopy(current)
        for field in ("title", "content", "status", "cover_asset_id"):
            value = getattr(payload, field)
            if value is not None:
                candidate[field] = value.strip() if isinstance(value, str) else value
        candidate["title"] = _clean_text(candidate.get("title"), 120) or "未命名小记"
        candidate["content"] = str(candidate.get("content") or "")[:12_000]
        candidate["revision"] = payload.expected_revision + 1
        candidate["updated_at"] = _now()
        with self.database.transaction(
            operation="update_journal",
            details={"character_id": character_id, "entry_id": entry_id},
        ):
            self.database.put_document(key, candidate)
        return deepcopy(candidate)

    def delete_journal(self, character_id: str, entry_id: str) -> bool:
        with self.database.transaction(
            operation="delete_journal",
            details={"character_id": character_id, "entry_id": entry_id},
        ):
            return self.database.delete_document(self._journal_key(character_id, entry_id))

    def generate_journal(
        self,
        character_id: str,
        *,
        session_id: str = "",
        activity_session_id: str = "",
    ) -> dict[str, Any]:
        """Generate one editable draft; failure deterministically falls back to a template."""

        character = self._character(character_id)
        if session_id:
            session_loader = getattr(self.sessions, "load_session", None)
            if callable(session_loader):
                session = session_loader(session_id)
                bound_character_id = str(session.get("character_id") or "")
                if bound_character_id and bound_character_id != character_id:
                    raise ValueError("journal source session belongs to another character")
        history = self.sessions.load_all(session_id) if session_id else []
        visible = self._journal_dialogue_window(history, limit_rounds=8)
        name = str(character.get("display_name") or "角色")
        fallback = self._journal_fallback(name, visible)
        content = ""
        generation = "template"
        model_attempted = False
        if visible and self.llm_provider is not None and self.api_provider is not None:
            model_attempted = True
            profile = character.get("ai_profile")
            profile_excerpt = {}
            if isinstance(profile, dict):
                for key in ("identity", "personality", "background", "speech_style"):
                    if key in profile:
                        profile_excerpt[key] = profile[key]
            role_context = json.dumps(
                {
                    "角色名": name,
                    "与用户关系": character.get("relationship_label") or "",
                    "角色设定摘录": profile_excerpt,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )[:2_400]
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"你正在替 AI 角色“{name}”写TA自己的私人日记。"
                        f"日记里的“我”只能指{name}；对话证据中的“用户本人”不是日记作者，"
                        f"“{name}本人”才是日记作者。不得把{name}写成第三人称TA、不得站在用户视角叙述。"
                        "只记录证据中确实发生或确实说过的内容，不补造动作、食物细节、日期、天气、"
                        "地点、情绪反应、承诺或用户经历。允许写角色对这些真实内容的主观感受，"
                        "但必须符合角色设定。写成自然、具体的中文第一人称日记，180至450字。"
                        "输出纯正文，不要标题、Markdown、JSON、对话标签或规则说明。\n"
                        f"角色上下文：{role_context}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"以下内容是唯一可引用证据。请先在内部确认：“我”={name}，“用户本人”=与{name}交谈的人。\n"
                    )
                    + "\n".join(
                        (f"[第{item['round']}轮] {self._journal_speaker_label(item['role'], name)}：{item['content']}")
                        for item in visible
                    ),
                },
            ]
            try:
                content = str(self.llm_provider().generate(messages, self.api_provider())).strip()
                content = re.sub(r"^```(?:markdown|text)?|```$", "", content).strip()
                if len(content) < 20:
                    raise ValueError("journal draft is too short")
                if "我" not in content:
                    raise ValueError("journal draft did not preserve the character first-person")
                content = content[:4_000]
                generation = "llm"
            except Exception as exc:  # noqa: BLE001
                content = ""
                self.audit.record(
                    "journal_generation_fallback",
                    {"character_id": character_id, "error": str(exc)[:500]},
                )
        entry = self.create_journal(
            character_id,
            JournalCreate(
                title=datetime.now().strftime("%m月%d日的小记"),
                content=content or fallback,
                status="draft",
                source="assistant_draft" if generation == "llm" else "template",
                session_id=session_id,
                activity_session_id=activity_session_id,
                source_round_start=visible[0]["round"] if visible else None,
                source_round_end=visible[-1]["round"] if visible else None,
                source_message_count=len(visible),
            ),
        )
        return {
            "entry": entry,
            "generation": generation,
            "model_calls": 1 if model_attempted else 0,
            "source_scope": {
                "session_id": session_id,
                "round_start": entry["source_round_start"],
                "round_end": entry["source_round_end"],
                "message_count": entry["source_message_count"],
            },
        }

    @staticmethod
    def _journal_speaker_label(role: str, name: str) -> str:
        return "用户本人（不是日记作者）" if role == "user" else f"{name}本人（日记作者）"

    @staticmethod
    def _journal_dialogue_window(history: list[dict[str, Any]], *, limit_rounds: int) -> list[dict[str, Any]]:
        """Keep complete, user-anchored rounds so initiative output cannot become a diary."""

        grouped: dict[int, list[dict[str, Any]]] = {}
        order: list[int] = []
        for index, item in enumerate(history):
            if item.get("hidden") or item.get("role") not in {"user", "assistant"}:
                continue
            try:
                round_number = int(item.get("round"))
            except (TypeError, ValueError):
                round_number = index
            if round_number not in grouped:
                grouped[round_number] = []
                order.append(round_number)
            grouped[round_number].append(
                {
                    "role": str(item.get("role")),
                    "content": str(item.get("content") or "")[:2_000],
                    "round": round_number,
                }
            )
        user_anchored = [
            grouped[round_number]
            for round_number in order
            if any(item["role"] == "user" for item in grouped[round_number])
        ][-limit_rounds:]
        return [item for group in user_anchored for item in group][-16:]

    @staticmethod
    def _journal_fallback(name: str, history: list[dict[str, Any]]) -> str:
        if not history:
            return f"这是一本属于{name}的空白小记。等有了想留下的片刻，再从这里慢慢写起。"
        latest_user = next(
            (item["content"][:160] for item in reversed(history) if item["role"] == "user" and item.get("content")),
            "",
        )
        latest_self = next(
            (
                item["content"][:160]
                for item in reversed(history)
                if item["role"] == "assistant" and item.get("content")
            ),
            "",
        )
        parts = [f"今天我和你又聊了一会儿。你对我说：“{latest_user}”。"]
        if latest_self:
            parts.append(f"我当时回答：“{latest_self}”。")
        parts.append("这些是我们今天真实留下的话，其他感受我想等更清楚时再慢慢写。")
        return "".join(parts)

    def list_moments(self, character_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self._character(character_id)
        items = [
            deepcopy(value)
            for _key, value in self.database.list_documents(f"moment:{character_id}:")
            if isinstance(value, dict)
        ]
        if not include_archived:
            items = [item for item in items if item.get("status") != "archived"]
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def search_narratives(self, character_id: str, query: str, *, limit: int = 3) -> list[RetrievedChunk]:
        """Return only lexical hits and keep them explicitly below fact authority.

        Journals are subjective role narration, so this deliberately avoids the
        global vector index. A small deterministic matcher is sufficient for
        related recall and prevents every saved diary from entering each turn.
        """

        self._character(character_id)
        query_tokens = self._narrative_tokens(query)
        if not query_tokens:
            return []
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        records = [
            *(("journal", item) for item in self.list_journals(character_id) if item.get("status") == "saved"),
            *(("moment", item) for item in self.list_moments(character_id) if item.get("status") == "saved"),
        ]
        for kind, item in records:
            text = (f"{item.get('title', '')}\n{item.get('content') or item.get('summary') or ''}").strip()
            tokens = self._narrative_tokens(text)
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            score = min(0.92, 0.5 + overlap / max(4, len(query_tokens)) * 0.42)
            candidates.append((score, kind, item))
        candidates.sort(key=lambda row: (row[0], str(row[2].get("updated_at"))), reverse=True)
        return [
            RetrievedChunk(
                chunk_id=f"narrative:{kind}:{item.get('entry_id') or item.get('moment_id')}",
                text=(f"{item.get('title', '')}\n{item.get('content') or item.get('summary') or ''}").strip()[:2_500],
                source="memory",
                score=score,
                weighted_score=score,
                session_id=str(item.get("session_id") or "") or None,
                physical_time=str(item.get("updated_at") or ""),
                metadata={
                    "visibility": "narrative_only",
                    "eligible_for_json_evidence": False,
                    "character_id": character_id,
                    "record_kind": kind,
                },
            )
            for score, kind, item in candidates[: max(0, min(limit, 5))]
        ]

    @staticmethod
    def _narrative_tokens(value: str) -> set[str]:
        normalized = re.sub(r"\s+", "", str(value or "").lower())
        chinese = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
        tokens = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
        tokens.update(re.findall(r"[a-z0-9_]{2,}", normalized))
        return tokens

    def create_moment(self, character_id: str, payload: MomentCreate) -> dict[str, Any]:
        self._character(character_id)
        now = _now()
        moment = {
            "moment_id": uuid4().hex,
            "character_id": character_id,
            "revision": 1,
            **payload.model_dump(mode="json"),
            "title": _clean_text(payload.title, 120),
            "summary": str(payload.summary).strip()[:2_000],
            "visibility": "narrative_only",
            "eligible_for_json_evidence": False,
            "created_at": now,
            "updated_at": now,
        }
        with self.database.transaction(
            operation="create_relationship_moment",
            details={"character_id": character_id, "status": moment["status"]},
        ):
            self.database.put_document(self._moment_key(character_id, moment["moment_id"]), moment)
        return deepcopy(moment)

    def update_moment(self, character_id: str, moment_id: str, payload: MomentUpdate) -> dict[str, Any]:
        key = self._moment_key(character_id, moment_id)
        current = self.database.get_document(key)
        if not isinstance(current, dict):
            raise KeyError("relationship moment not found")
        if int(current.get("revision", 0)) != payload.expected_revision:
            raise ValueError("relationship moment revision conflict")
        candidate = deepcopy(current)
        for field in ("title", "summary", "status", "art_asset_id"):
            value = getattr(payload, field)
            if value is not None:
                candidate[field] = value.strip()
        candidate["revision"] = payload.expected_revision + 1
        candidate["updated_at"] = _now()
        with self.database.transaction(
            operation="update_relationship_moment",
            details={"character_id": character_id, "moment_id": moment_id},
        ):
            self.database.put_document(key, candidate)
        return deepcopy(candidate)

    def scenes(self) -> list[dict[str, Any]]:
        """Return visual environments; selecting one never starts an activity."""

        return deepcopy(list(SCENE_DEFINITIONS)) + self._custom_scenes()

    def create_custom_scene(
        self,
        *,
        scene_id: str,
        title: str,
        description: str,
        asset_id: str,
        asset_url: str,
    ) -> dict[str, Any]:
        """Register a user-owned background without mutating the bundled art archive."""

        clean_title = _clean_text(title, 80) or "自定义场景"
        clean_description = _clean_text(description, 300) or f"与角色共同置身于{clean_title}。"
        record = {
            "scene_id": scene_id,
            "title": clean_title,
            "description": clean_description,
            "location": clean_title,
            "asset_id": asset_id,
            "asset_url": asset_url,
            "custom": True,
            "created_at": _now(),
        }
        with self.database.transaction(
            operation="create_custom_scene",
            details={"scene_id": scene_id},
        ):
            self.database.put_document(self._custom_scene_key(scene_id), record)
        self.audit.record("custom_scene_created", {"scene_id": scene_id})
        return deepcopy(record)

    def _bound_scene_session(self, session_id: str) -> tuple[dict[str, Any], str]:
        session = self.sessions.load_session(session_id)
        character_id = str(session.get("character_id") or "")
        if not character_id:
            raise KeyError("conversation session not found or is not bound to a character")
        self._character(character_id)
        return session, character_id

    def get_session_scene(self, session_id: str) -> dict[str, Any]:
        _session, character_id = self._bound_scene_session(session_id)
        current = self.database.get_document(self._scene_key(session_id))
        if isinstance(current, dict):
            return deepcopy(current)
        preference = self.database.get_document(self._character_scene_key(character_id))
        scene_id = str(preference.get("scene_id") or "") if isinstance(preference, dict) else ""
        scene = self._find_scene(scene_id)
        return {
            "session_id": session_id,
            "character_id": character_id,
            "revision": 0,
            "scene": deepcopy(scene) if scene is not None else None,
            "inherited_from_character": scene is not None,
            "updated_at": "",
        }

    def set_session_scene(self, session_id: str, payload: SceneSelectionUpdate) -> dict[str, Any]:
        _session, character_id = self._bound_scene_session(session_id)
        scene = self._find_scene(payload.scene_id)
        if scene is None:
            raise ValueError("unknown conversation scene")
        key = self._scene_key(session_id)
        current = self.database.get_document(key)
        revision = int(current.get("revision", 0)) if isinstance(current, dict) else 0
        if revision != payload.expected_revision:
            raise ValueError("conversation scene revision conflict")
        now = _now()
        record = {
            "session_id": session_id,
            "character_id": character_id,
            "revision": revision + 1,
            "scene": deepcopy(scene),
            "inherited_from_character": False,
            "updated_at": now,
        }
        preference = {
            "character_id": character_id,
            "scene_id": scene["scene_id"],
            "updated_at": now,
        }
        with self.database.transaction(
            operation="set_conversation_scene",
            details={
                "session_id": session_id,
                "character_id": character_id,
                "scene_id": scene["scene_id"],
            },
        ):
            self.database.put_document(key, record)
            self.database.put_document(self._character_scene_key(character_id), preference)
        self.audit.record(
            "conversation_scene_changed",
            {
                "session_id": session_id,
                "character_id": character_id,
                "scene_id": scene["scene_id"],
            },
        )
        return deepcopy(record)

    def scene_prompt_context(self, session_id: str, *, character_id: str) -> dict[str, Any] | None:
        scene_record = self.get_session_scene(session_id)
        if str(scene_record.get("character_id") or "") != character_id:
            raise ValueError("conversation scene belongs to another character")
        scene = scene_record.get("scene")
        if not isinstance(scene, dict):
            return None
        return {
            "scene_id": scene["scene_id"],
            "title": scene["title"],
            "location": scene["location"],
            "asset_id": scene["asset_id"],
            "visibility": "ephemeral_conversation_scene",
            "eligible_for_json_evidence": False,
        }

    def activities(self) -> list[dict[str, Any]]:
        return deepcopy(list(ACTIVITY_DEFINITIONS))

    def start_activity(self, activity_id: str, payload: ActivityStart) -> dict[str, Any]:
        self._character(payload.character_id)
        if activity_id == "scene_companion":
            raise ValueError("scene companion is now a conversation scene, not an activity")
        definition = self._definition(activity_id)
        now = _now()
        activity_session_id = uuid4().hex
        session = {
            "activity_session_id": activity_session_id,
            "activity_id": activity_id,
            "character_id": payload.character_id,
            "session_id": payload.session_id,
            "revision": 1,
            "phase": definition["initial_phase"],
            "status": "active",
            "state": {},
            "processed_actions": {},
            "created_at": now,
            "updated_at": now,
        }
        with self.database.transaction(
            operation="start_activity",
            details={"activity_id": activity_id, "character_id": payload.character_id},
        ):
            self.database.put_document(self._activity_key(activity_session_id), session)
        return deepcopy(session)

    def get_activity_session(self, activity_session_id: str) -> dict[str, Any]:
        value = self.database.get_document(self._activity_key(activity_session_id))
        if not isinstance(value, dict):
            raise KeyError("activity session not found")
        return deepcopy(value)

    def list_activity_sessions(self, character_id: str, *, include_finished: bool = True) -> list[dict[str, Any]]:
        self._character(character_id)
        items = [
            deepcopy(value)
            for _key, value in self.database.list_documents("activity-session:")
            if isinstance(value, dict) and value.get("character_id") == character_id
        ]
        if not include_finished:
            items = [item for item in items if item.get("status") == "active"]
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def apply_activity_action(self, activity_session_id: str, payload: ActivityAction) -> dict[str, Any]:
        key = self._activity_key(activity_session_id)
        current = self.get_activity_session(activity_session_id)
        replay = (current.get("processed_actions") or {}).get(payload.action_id)
        if isinstance(replay, dict):
            return {"session": current, "result": deepcopy(replay), "idempotent_replay": True}
        if int(current.get("revision", 0)) != payload.expected_revision:
            raise ValueError("activity revision conflict")
        if current.get("status") != "active" and not (
            current.get("status") == "interrupted" and payload.action == "resume"
        ):
            raise ValueError("activity session is not active")
        definition = self._definition(str(current["activity_id"]))
        candidate = deepcopy(current)
        result = self._reduce_activity(candidate, definition, payload)
        candidate["revision"] = payload.expected_revision + 1
        candidate["updated_at"] = _now()
        processed = dict(candidate.get("processed_actions") or {})
        processed[payload.action_id] = deepcopy(result)
        candidate["processed_actions"] = dict(list(processed.items())[-128:])
        with self.database.transaction(
            operation="apply_activity_action",
            details={
                "activity_session_id": activity_session_id,
                "action": payload.action,
            },
        ):
            self.database.put_document(key, candidate)
            if candidate["status"] == "completed" and not candidate["state"].get("candidate_moment_id"):
                moment = self.create_moment(
                    str(candidate["character_id"]),
                    MomentCreate(
                        title=f"完成了「{definition['title']}」",
                        summary=self._activity_summary(candidate, definition),
                        event_type="activity_completion",
                        status="candidate",
                        source="activity_completion",
                        session_id=str(candidate.get("session_id") or ""),
                        activity_session_id=activity_session_id,
                        evidence_refs=[f"activity:{activity_session_id}"],
                    ),
                )
                candidate["state"]["candidate_moment_id"] = moment["moment_id"]
                self.database.put_document(key, candidate)
                result["candidate_moment"] = moment
        return {"session": deepcopy(candidate), "result": result, "idempotent_replay": False}

    def prompt_context(self, activity_session_id: str, *, character_id: str) -> dict[str, Any]:
        session = self.get_activity_session(activity_session_id)
        if session.get("character_id") != character_id:
            raise ValueError("activity session belongs to another character")
        definition = self._definition(str(session["activity_id"]))
        return {
            "activity_session_id": activity_session_id,
            "activity_id": definition["activity_id"],
            "title": definition["title"],
            "description": definition["description"],
            "phase": session["phase"],
            "status": session["status"],
            "state": deepcopy(session.get("state") or {}),
            "rules": [
                "活动状态由服务端维护，只能依据当前状态自然回应。",
                "不要声称已经执行未出现在状态中的动作。",
                "不要输出状态 JSON、动作标签、积分或内部阶段名。",
                "用户界面负责推进活动；本轮只输出角色本人此刻的表达。",
            ],
            "visibility": "ephemeral_activity_session",
            "eligible_for_json_evidence": False,
        }

    def _reduce_activity(
        self,
        session: dict[str, Any],
        definition: dict[str, Any],
        payload: ActivityAction,
    ) -> dict[str, Any]:
        state = session.setdefault("state", {})
        if payload.action == "resume":
            previous_phase = str(state.pop("interrupted_phase", "") or "")
            if session.get("status") != "interrupted" or not previous_phase:
                raise ValueError("activity session cannot be resumed")
            session["status"] = "active"
            session["phase"] = previous_phase
            return {"status": "active", "phase": previous_phase}
        if payload.action == "cancel":
            state["interrupted_phase"] = session["phase"]
            session["status"] = "interrupted"
            session["phase"] = "interrupted"
            return {"status": "interrupted"}
        if payload.action == "complete":
            self._validate_completion(session, definition)
            session["status"] = "completed"
            session["phase"] = "completed"
            return {"status": "completed"}
        if definition["activity_id"] == "scene_companion" and payload.action == "select_scene":
            scene_id = _clean_text(payload.payload.get("scene_id"), 64)
            scene = next(
                (item for item in definition["scenes"] if item["scene_id"] == scene_id),
                None,
            )
            if scene is None:
                raise ValueError("unknown activity scene")
            state["scene"] = deepcopy(scene)
            session["phase"] = "conversation"
            return {"scene": deepcopy(scene)}
        if definition["activity_id"] == "mutual_questions":
            questions = definition["questions"]
            if payload.action == "draw_question":
                index = len(state.get("answers") or []) % len(questions)
                state["current_question"] = questions[index]
                state["question_index"] = index
                session["phase"] = "answering"
                return {"question": questions[index], "question_index": index}
            if payload.action == "answer_question":
                answer = str(payload.payload.get("answer") or "").strip()[:2_000]
                if not answer or not state.get("current_question"):
                    raise ValueError("question and answer are required")
                answers = list(state.get("answers") or [])
                answers.append({"question": state["current_question"], "user_answer": answer})
                state["answers"] = answers[-20:]
                state.pop("current_question", None)
                session["phase"] = "ready"
                return {"answer_count": len(answers)}
        if definition["activity_id"] == "story_choices" and payload.action == "choose_story":
            current_node = str(session["phase"]).removeprefix("node:")
            node = definition["nodes"].get(current_node)
            choice_id = _clean_text(payload.payload.get("choice_id"), 64)
            choice = next(
                (item for item in (node or {}).get("choices", []) if item["choice_id"] == choice_id),
                None,
            )
            if choice is None:
                raise ValueError("choice is not available in the current story node")
            path = list(state.get("path") or [])
            path.append(choice_id)
            state["path"] = path
            state["current_text"] = definition["nodes"][choice["next"]]["text"]
            session["phase"] = f"node:{choice['next']}"
            return {"choice_id": choice_id, "next": choice["next"]}
        raise ValueError("action is not allowed for this activity")

    @staticmethod
    def _validate_completion(session: dict[str, Any], definition: dict[str, Any]) -> None:
        state = session.get("state") or {}
        activity_id = definition["activity_id"]
        if activity_id == "scene_companion" and not state.get("scene"):
            raise ValueError("select a scene before completing the activity")
        if activity_id == "mutual_questions" and not state.get("answers"):
            raise ValueError("answer at least one question before completing the activity")
        if activity_id == "story_choices" and session.get("phase") != "node:ending":
            raise ValueError("reach the story ending before completing the activity")

    @staticmethod
    def _activity_summary(session: dict[str, Any], definition: dict[str, Any]) -> str:
        state = session.get("state") or {}
        if definition["activity_id"] == "scene_companion":
            scene = state.get("scene") or {}
            return f"一起进入了「{scene.get('title', '未命名场景')}」的陪伴场景。"
        if definition["activity_id"] == "mutual_questions":
            return f"完成了一次默契问答，留下了 {len(state.get('answers') or [])} 个真实回答。"
        path = " → ".join(state.get("path") or []) or "自然结束"
        return f"一起完成了「{definition['title']}」，选择路径为：{path}。"

    @staticmethod
    def _definition(activity_id: str) -> dict[str, Any]:
        if activity_id == "scene_companion":
            return deepcopy(LEGACY_SCENE_ACTIVITY)
        definition = next(
            (item for item in ACTIVITY_DEFINITIONS if item["activity_id"] == activity_id),
            None,
        )
        if definition is None:
            raise KeyError("activity definition not found")
        return deepcopy(definition)

    def _migrate_confirmed_continuity(self) -> None:
        marker = "migration:shared-chapters:0.7.0"
        if self.database.has_document(marker):
            return
        with self.database.transaction(operation="migrate_shared_chapters"):
            for character in self.characters.list(include_archived=True):
                character_id = str(character["character_id"])
                values: list[tuple[str, str]] = []
                continuity = (character.get("ai_profile") or {}).get("continuity") or {}
                runtime = character.get("runtime_state") or {}
                relationship = runtime.get("relationship_state") or {}
                for source, raw in (
                    (
                        "important_shared_experiences",
                        continuity.get("important_shared_experiences") or [],
                    ),
                    ("recent_positive_events", relationship.get("recent_positive_events") or []),
                ):
                    if isinstance(raw, list):
                        values.extend((source, _clean_text(item, 2_000)) for item in raw)
                for source, text in values:
                    if not text:
                        continue
                    digest = hashlib.sha256(f"{character_id}:{source}:{text}".encode()).hexdigest()[:32]
                    key = self._moment_key(character_id, digest)
                    if self.database.has_document(key):
                        continue
                    now = _now()
                    self.database.put_document(
                        key,
                        {
                            "moment_id": digest,
                            "character_id": character_id,
                            "revision": 1,
                            "title": "已有共同经历",
                            "summary": text,
                            "event_type": "legacy_continuity",
                            "status": "saved",
                            "source": "profile_migration",
                            "session_id": "",
                            "activity_session_id": "",
                            "journal_entry_id": "",
                            "art_asset_id": "moment-heart-keepsake",
                            "evidence_refs": [f"profile:{source}"],
                            "visibility": "narrative_only",
                            "eligible_for_json_evidence": False,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
            self.database.put_document(marker, {"completed_at": _now(), "version": "0.7.0"})
