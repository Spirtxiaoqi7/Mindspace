import json

from mindspace_graph.native_tools import (
    native_call_to_instruction,
    native_tool_choice,
    native_tool_definitions,
    supports_native_tools,
)


def test_hinted_native_tool_sets_are_small_and_required():
    assert [item["function"]["name"] for item in native_tool_definitions("web")] == ["web"]
    assert [item["function"]["name"] for item in native_tool_definitions("memory")] == ["memory"]
    assert [item["function"]["name"] for item in native_tool_definitions("task")] == [
        "task_list",
        "task_create",
        "task_update",
        "task_complete",
    ]
    assert native_tool_choice("web") == "required"
    assert native_tool_choice("") == "auto"


def test_native_calls_map_to_validated_host_instructions():
    memory = native_call_to_instruction(
        {"id": "m1", "function": {"name": "memory", "arguments": '{"query":"上次的旅行计划"}'}},
        user_message="帮我回忆上次的旅行计划",
    )
    assert memory.call_id == "m1"
    assert memory.tool == "memory"
    assert memory.level == 3
    assert memory.parameter == "上次的旅行计划"
    task = native_call_to_instruction(
        {
            "id": "t1",
            "function": {
                "name": "task_create",
                "arguments": '{"title":"交报告","due_at":"2026-08-10T18:00:00+08:00"}',
            }
        },
        user_message="2026年8月10日18:00交报告",
    )
    assert task.tool == "task"
    assert task.level == 2
    assert json.loads(task.parameter) == {
        "op": "create",
        "title": "交报告",
        "due_at": "2026-08-10T18:00:00+08:00",
    }


def test_native_task_discards_model_invented_clock_time():
    task = native_call_to_instruction(
        {
            "id": "t2",
            "function": {
                "name": "task_create",
                "arguments": '{"title":"周六买花","due_at":"2026-08-15T10:00:00+08:00"}',
            },
        },
        user_message="周六买花这件事我怕忘，放进待办里",
    )
    assert task.command == {"op": "create", "title": "周六买花", "due_at": None}


def test_native_tools_only_enable_for_official_deepseek_endpoint():
    assert supports_native_tools("https://api.deepseek.com")
    assert supports_native_tools("https://api.deepseek.com/v1/")
    assert not supports_native_tools("https://api.siliconflow.com/v1")
