from mindspace_graph.memory_update import parse_memory_plan, should_extract_memory


def test_memory_extraction_gate_skips_acknowledgements_and_time_questions():
    assert should_extract_memory("嗯") is False
    assert should_extract_memory("现在几点了？") is False
    assert should_extract_memory("看看互联网上最近有什么有趣的事情") is False


def test_memory_extraction_gate_accepts_user_preferences_and_agent_self_questions():
    assert should_extract_memory("我不喜欢别人催我睡觉") is True
    assert should_extract_memory("你这是没有安全感吗？") is True
    assert should_extract_memory("记一下，我们刚刚决定周五再处理") is True


def test_memory_plan_parser_accepts_fenced_json():
    plan = parse_memory_plan(
        """```json
        {"turn_id":"round_1","base_revisions":{"user_profile":0,"ai_profile":0,"runtime_state":0},"trigger":"none","patches":[]}
        ```"""
    )
    assert plan.trigger == "none"
