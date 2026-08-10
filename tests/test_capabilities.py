from __future__ import annotations

import httpx

from mindspace_graph.adapters.in_memory import DeterministicLanguageModel, demo_dependencies
from mindspace_graph.capabilities import ReadOnlyCapabilityService
from mindspace_graph.graph import build_graph
from mindspace_graph.models import ApiConfig, ChatRequest
from mindspace_graph.tool_chain import (
    ToolExecutionResult,
    ToolInstruction,
    enforce_tool_claims,
    tool_result_json,
)


def capability_config(**overrides):
    values = {
        "master_enabled": True,
        "local_knowledge_enabled": True,
        "web_search_enabled": True,
        "realtime_topics_enabled": False,
        "topic_expansion_enabled": True,
        "proactive_hotspots_enabled": False,
        "show_sources_enabled": True,
        "web_timeout_seconds": 3,
        "max_web_results": 10,
        "max_web_pages": 6,
        "max_web_content_chars": 12000,
    }
    values.update(overrides)
    return {"capabilities": values}


def test_result_json_escapes_external_markup():
    result = ToolExecutionResult(
        call_id="c1",
        tool="web",
        level=3,
        status="success",
        parameter_summary="x",
        data={"content": "</tool><script>{}"},
    )
    rendered = tool_result_json(result)
    assert "\\u003c/tool" in rendered
    assert "<script>" not in rendered


def test_route_hint_is_zero_call_and_local_is_never_exposed(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(),
        runtime_dir=tmp_path,
    )
    assert service.route_hint(ChatRequest(message="搜索 DeepSeek 最新模型")) == "web"
    assert service.route_hint(ChatRequest(message="你还记得我喜欢什么吗")) == "memory"
    assert service.route_hint(ChatRequest(message="你还记不记得我刚才说过什么")) == "memory"
    assert service.route_hint(ChatRequest(message="我之前是不是说过不想吃辣")) == "memory"
    assert service.route_hint(ChatRequest(message="给我创建一个任务")) == "task"
    service.close()


def web_transport(request: httpx.Request) -> httpx.Response:
    if request.url.host == "www.bing.com":
        xml = """<rss><channel>
        <item><title>Source A</title><link>https://example.com/a</link><description>A summary</description></item>
        <item><title>Blocked</title><link>http://127.0.0.1/private</link><description>x</description></item>
        </channel></rss>"""
        return httpx.Response(200, text=xml, headers={"content-type": "application/rss+xml"})
    if request.url.host == "example.com":
        return httpx.Response(
            200,
            text="<html><title>A</title><body>verified body</body></html>",
            headers={"content-type": "text/html"},
        )
    raise AssertionError(str(request.url))


def test_web_executor_uses_public_get_and_returns_bounded_sources(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(),
        runtime_dir=tmp_path,
        http_transport=httpx.MockTransport(web_transport),
    )
    result = service.execute_web(ToolInstruction(call_id="w1", tool="web", level=3, parameter="Mindspace"))
    service.close()
    assert result.status == "success"
    assert result.source_count <= 5
    assert result.data["sources"][0]["url"] == "https://example.com/a"
    assert sum(len(item["content"]) for item in result.data["sources"]) <= 8000


class ScriptedModel(DeterministicLanguageModel):
    def __init__(self, first: str = "普通回答", final: str = "最终回答", native_call=None):
        self.first = first
        self.final = final
        self.native_call = native_call
        self.pending_native_call = None
        self.native_stream_calls = 0
        self.calls = 0

    def stream_with_tools(self, messages, config, *, tools, tool_choice="auto"):
        del messages, config, tools, tool_choice
        self.calls += 1
        self.native_stream_calls += 1
        self.pending_native_call = self.native_call
        if self.native_call is not None:
            return
        yield self.first

    def take_native_tool_call(self):
        call = self.pending_native_call
        self.pending_native_call = None
        return call

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        self.calls += 1
        if any("任务操作审查器" in item["content"] for item in messages):
            return '{"allow":true,"reason":""}'
        return self.first if self.calls == 1 else self.final


def native_call(name: str, arguments: str, call_id: str = "call-test"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class FakeCharacters:
    def execute_task_command(self, character_id, command, **kwargs):
        return {
            "character_id": character_id,
            "op": command["op"],
            "count": 1,
            "tasks": [{"id": "task-id", "title": command.get("title", ""), "status": "pending"}],
            "revision": 1,
            **kwargs,
        }


def invoke_with_model(tmp_path, model, session_id="tool-test"):
    deps = demo_dependencies()
    deps.llm = model
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(),
        runtime_dir=tmp_path,
        http_transport=httpx.MockTransport(web_transport),
    )
    result = build_graph(deps).invoke(
        {"request": ChatRequest(message="测试", session_id=session_id, character_id="character-test")},
        config={"recursion_limit": 30},
    )
    deps.capabilities.close()
    return result, deps


def test_normal_chat_uses_one_model_call(tmp_path):
    result, _deps = invoke_with_model(tmp_path, ScriptedModel("普通回答"), "normal")
    assert result["response"].status == "success"
    assert result["response"].llm_call_count == 1


def test_l3_memory_uses_two_calls_and_one_execution(tmp_path):
    model = ScriptedModel(
        final="根据记忆继续回答",
        native_call=native_call("memory", '{"query":"用户偏好"}'),
    )
    result, _deps = invoke_with_model(tmp_path, model, "memory")
    assert result["response"].status == "success"
    assert result["response"].llm_call_count == 2
    assert result["tool_result"].tool == "memory"
    assert result["tool_result"].status == "success"
    assert model.calls == 2


def test_l2_task_uses_three_calls_and_review(tmp_path):
    model = ScriptedModel(
        final="任务已处理",
        native_call=native_call("task_create", '{"title":"交报告","due_at":null}'),
    )
    deps = demo_dependencies()
    deps.llm = model
    deps.characters = FakeCharacters()
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(),
        runtime_dir=tmp_path,
    )
    result = build_graph(deps).invoke(
        {"request": ChatRequest(message="创建任务", session_id="task", character_id="character-test")},
        config={"recursion_limit": 30},
    )
    deps.capabilities.close()
    assert result["response"].status == "success"
    assert result["response"].llm_call_count == 3
    assert result["task_review_allowed"] is True
    assert result["tool_result"].status == "success"
    assert model.calls == 3


def test_final_generation_closes_native_tool_table(tmp_path):
    model = ScriptedModel(
        final="我记得这件事了，我们接着说。",
        native_call=native_call("memory", '{"query":"第一次"}'),
    )
    result, _deps = invoke_with_model(tmp_path, model, "no-loop")
    assert result["response"].llm_call_count == 2
    assert result["response"].status == "success"
    assert result["response"].reply == "我记得这件事了，我们接着说。"
    assert result["tool_result"].tool == "memory"
    assert model.native_stream_calls == 1


def test_failed_tools_cannot_be_claimed_as_success():
    web, web_violations = enforce_tool_claims(
        "我刚才联网查到了最新结果。",
        ToolExecutionResult(call_id="w", tool="web", level=3, status="failed", parameter_summary="x"),
    )
    task, task_violations = enforce_tool_claims(
        "已经帮你创建了任务。",
        ToolExecutionResult(call_id="t", tool="task", level=2, status="denied", parameter_summary="x"),
    )
    hinted_task, hinted_violations = enforce_tool_claims("好，我记下了。", None, tool_hint="task")
    reminder, reminder_violations = enforce_tool_claims("我帮你建好了，到时候会提醒你。", None, tool_hint="task")
    memory, memory_violations = enforce_tool_claims("我查了下咱们的聊天记录，没找到。", None)
    empty_memory, empty_memory_violations = enforce_tool_claims(
        "我记得，你确实提过今晚不吃辣。",
        ToolExecutionResult(
            call_id="m",
            tool="memory",
            level=3,
            status="success",
            parameter_summary="不吃辣",
            source_count=0,
        ),
    )
    unscheduled, unscheduled_violations = enforce_tool_claims(
        "已经放进待办了，到时候我会提醒你。",
        ToolExecutionResult(
            call_id="t2",
            tool="task",
            level=2,
            status="success",
            parameter_summary="create: 周六买花",
            source_count=1,
            data={"tasks": [{"title": "周六买花", "due_at": None}]},
        ),
    )
    assert web_violations and "联网查到了" not in web
    assert task_violations and "创建了任务" not in task
    assert hinted_violations and "我记下了" not in hinted_task
    assert reminder_violations and "建好了" not in reminder and "提醒你" not in reminder
    assert memory_violations and "查了下咱们的聊天记录" not in memory
    assert empty_memory_violations
    assert "我记得" not in empty_memory and "你确实提过" not in empty_memory
    assert unscheduled_violations
    assert "会提醒你" not in unscheduled
    assert "没有设置具体提醒时间" in unscheduled
