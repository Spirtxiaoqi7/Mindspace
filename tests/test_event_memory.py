from mindspace_graph.event_memory import (
    EventMemoryStore,
    event_memory_lane,
    parse_event_operation,
    normalize_event_operation,
    resolve_event_target,
    should_consider_event,
)
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
