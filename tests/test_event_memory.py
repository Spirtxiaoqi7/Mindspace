from mindspace_graph.event_memory import (
    EventMemoryStore,
    build_event_extraction_messages,
    event_memory_lane,
    normalize_event_operation,
    parse_event_operation,
    resolve_event_target,
    should_consider_event,
)
from mindspace_graph.models import ApiConfig, ChatRequest, ChatResponse
from mindspace_graph.product_database import ProductDatabase


def operation(title: str, *, group: str = "pending", category: str = "user_related", importance: int = 2):
    return {
        "operation": "add",
        "group": group,
        "category": category,
        "title": title,
        "summary": f"{title}的具体事件",
        "due_at": None,
        "importance": importance,
    }


def test_event_memory_enforces_three_plus_three(tmp_path):
    store = EventMemoryStore(ProductDatabase(tmp_path / "context.db"))
    for index in range(4):
        store.apply("char-a", operation(f"待办{index}", importance=1 if index == 0 else 2))
    for category in ("user_related", "ai_related", "relationship_related"):
        store.apply("char-a", operation(category, group="subject", category=category))
    snapshot = store.snapshot("char-a")
    assert len(snapshot["pending"]) == 3
    assert {item["title"] for item in snapshot["pending"]} == {"待办1", "待办2", "待办3"}
    assert all(snapshot["subjects"].values())
    assert len(snapshot["history"]) == 1


def test_subject_slot_replaces_only_its_category(tmp_path):
    store = EventMemoryStore(ProductDatabase(tmp_path / "context.db"))
    first = store.apply("char-a", operation("用户旧事件", group="subject"))["event"]
    store.apply("char-a", operation("用户新事件", group="subject"))
    snapshot = store.snapshot("char-a")
    assert snapshot["subjects"]["user_related"]["title"] == "用户新事件"
    assert snapshot["history"][0]["id"] == first["id"]
    assert store.snapshot("char-b")["subjects"]["user_related"] is None


def test_parser_and_candidate_gate_are_bounded():
    parsed = parse_event_operation(
        '```json\n{"operation":"add","group":"待办","category":"关系相关",'
        '"title":"周末看电影","summary":"双方约定周末一起看电影","importance":3}\n```'
    )
    assert parsed["group"] == "pending"
    assert parsed["category"] == "relationship_related"
    assert should_consider_event("记得明天提醒我交报告") is True
    assert should_consider_event("今天天气不错") is False
    assert event_memory_lane("记得明天提醒我交报告") is True
    assert event_memory_lane("联网查明天天气，顺便提醒我带伞") is False


def test_destructive_target_is_corrected_by_current_user_wording(tmp_path):
    store = EventMemoryStore(ProductDatabase(tmp_path / "context.db"))
    movie = store.apply("char-a", operation("周六下午看电影", category="relationship_related"))["event"]
    interview = store.apply("char-a", operation("下周三参加面试"))["event"]
    resolved = resolve_event_target(
        {
            "operation": "remove",
            "group": "pending",
            "category": "user_related",
            "target_id": interview["id"],
        },
        store.snapshot("char-a"),
        "周六电影不看了，取消看电影的约定",
    )
    assert resolved["target_id"] == movie["id"]
    assert resolved["_target_corrected"] is True


def test_event_slot_and_lifecycle_are_normalized_from_user_evidence():
    ai_event = normalize_event_operation(
        operation("记录睡前聊天", group="pending", category="ai_related"),
        "你刚刚决定开始记录睡前聊天",
    )
    assert ai_event["group"] == "subject"
    cancellation = normalize_event_operation(
        {"operation": "complete", "target_id": "evt_movie"},
        "周六电影不看了，取消这个约定",
    )
    assert cancellation["operation"] == "remove"


def test_event_extractor_receives_recent_dialogue_for_short_confirmation():
    request = ChatRequest(
        session_id="session-a",
        character_id="char-a",
        round=3,
        message="真好",
        api=ApiConfig(base_url="https://example.test/v1", api_key="test", model="test-model"),
    )
    response = ChatResponse(
        session_id="session-a",
        round=3,
        status="success",
        reply="那从今天开始，我就是你老婆。",
    )
    messages = build_event_extraction_messages(
        request,
        response,
        {"pending": [], "subjects": {}},
        [
            {"round": 2, "role": "user", "content": "当我的什么"},
            {"round": 2, "role": "assistant", "content": "我想当你老婆。"},
            {"round": 3, "role": "user", "content": "真好"},
            {"round": 3, "role": "assistant", "content": "那从今天开始，我就是你老婆。"},
        ],
        [{"source": "chat", "text": "两人此前一直以恋人相称", "round": 1}],
        {"ai_profile": {"relationship": "恋人"}, "revisions": {"ai_profile": 2}},
    )
    assert "recent_dialogue" in messages[1]["content"]
    assert "当我的什么" in messages[1]["content"]
    assert "retrieved_context" in messages[1]["content"]
    assert "structured_context" in messages[1]["content"]
    assert "不要把 current_user 当成孤立句子" in messages[0]["content"]
    assert "不能单独作为新增" in messages[0]["content"]
