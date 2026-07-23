from __future__ import annotations

import json
import time

import httpx

from mindspace_graph.adapters.in_memory import DeterministicLanguageModel, demo_dependencies
from mindspace_graph.capabilities import (
    CapabilityCall,
    CapabilityPlan,
    CapabilityResult,
    ReadOnlyCapabilityService,
    enforce_capability_claims,
)
from mindspace_graph.graph import build_graph
from mindspace_graph.models import ApiConfig, ChatRequest


def capability_config(**overrides):
    values = {
        "master_enabled": True,
        "local_status_enabled": True,
        "mindspace_health_enabled": True,
        "local_knowledge_enabled": True,
        "web_search_enabled": False,
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


def test_read_only_capabilities_execute_serially_and_keep_plan_order(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(), runtime_dir=tmp_path
    )

    def slow_snapshot():
        time.sleep(0.15)
        return {"kind": "snapshot"}

    def slow_health():
        time.sleep(0.15)
        return {"kind": "health"}

    service.capture_local_snapshot = slow_snapshot
    service._mindspace_health = slow_health
    plan = CapabilityPlan(
        decision="use_capabilities",
        calls=[
            CapabilityCall(
                call_id="first", capability="local.system_snapshot", arguments={}
            ),
            CapabilityCall(
                call_id="second", capability="local.mindspace_health", arguments={}
            ),
        ],
    )

    started = time.perf_counter()
    results = service.execute(plan, local_snapshot={}, ranked_context=[])
    elapsed = time.perf_counter() - started
    service.close()

    assert elapsed >= 0.29
    assert [item.call_id for item in results] == ["first", "second"]
    assert [item.data["kind"] for item in results] == ["snapshot", "health"]


def test_every_capability_call_waits_for_the_previous_result(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(), runtime_dir=tmp_path
    )
    timeline: list[tuple[str, str, float]] = []

    def timed_call(call, **_kwargs):
        timeline.append((call.call_id, "start", time.perf_counter()))
        time.sleep(0.08)
        timeline.append((call.call_id, "end", time.perf_counter()))
        return CapabilityResult(call_id=call.call_id, capability=call.capability)

    service._execute_call = timed_call
    plan = CapabilityPlan(
        decision="use_capabilities",
        calls=[
            CapabilityCall(call_id="parallel-a", capability="local.system_snapshot"),
            CapabilityCall(call_id="parallel-b", capability="knowledge.search_local"),
            CapabilityCall(call_id="exclusive", capability="local.mindspace_health"),
        ],
    )

    started = time.perf_counter()
    results = service.execute(plan, local_snapshot={}, ranked_context=[])
    elapsed = time.perf_counter() - started
    service.close()

    moments = {(call_id, phase): at for call_id, phase, at in timeline}
    assert elapsed >= 0.23
    assert moments[("parallel-b", "start")] >= moments[("parallel-a", "end")]
    assert moments[("exclusive", "start")] >= moments[("parallel-b", "end")]
    assert [item.call_id for item in results] == ["parallel-a", "parallel-b", "exclusive"]


def test_graph_does_not_collect_local_state_without_a_local_capability_call(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(), runtime_dir=tmp_path
    )

    def fail_if_sampled():
        raise AssertionError("local status must be demand driven")

    service.capture_local_snapshot = fail_if_sampled
    dependencies = demo_dependencies()
    dependencies.capabilities = service

    result = build_graph(dependencies).invoke(
        {
            "request": ChatRequest(
                message="陪我聊一句普通的话",
                session_id="conditional-local",
                round=1,
                retrieval={"rag_enabled": False},
            ),
            "request_id": "conditional-local-run",
        }
    )
    service.close()

    assert result["response"].status == "success"
    assert "capture_local_snapshot" not in result["trace"]


def rss_transport(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.url.host == "www.bing.com"
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss><channel>
      <item><title>Mindspace 最新进展</title>
      <link>https://example.com/mindspace-news</link>
      <description>这是公开来源中的最新摘要。</description>
      <pubDate>Wed, 22 Jul 2026 02:00:00 GMT</pubDate></item>
      <item><title>不安全结果</title><link>http://127.0.0.1/private</link>
      <description>不能返回本地地址。</description></item>
    </channel></rss>"""
    return httpx.Response(200, text=xml, headers={"Content-Type": "application/rss+xml"})


def research_transport(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    if request.url.host == "arxiv.org":
        return httpx.Response(
            200,
            text="""<html><head>
            <meta name="citation_title" content="A Reliable Memory Architecture" />
            <meta name="citation_author" content="Ada Example" />
            <meta name="citation_abstract" content="We present a verified memory architecture." />
            </head><body><main>The method separates retrieval from durable facts
            and reports ablation results.</main></body></html>""",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    if request.url.host == "www.bing.com":
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item><title>Independent discussion</title>
          <link>https://example.org/review</link>
          <description>A review of the memory paper.</description></item>
        </channel></rss>"""
        return httpx.Response(200, text=xml, headers={"Content-Type": "application/rss+xml"})
    if request.url.host == "example.org":
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Independent discussion</title></head>"
                "<body>The review confirms the stated architecture.</body></html>"
            ),
            headers={"Content-Type": "text/html"},
        )
    raise AssertionError(f"unexpected host: {request.url.host}")


def test_permissions_route_only_enabled_read_capabilities(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(), runtime_dir=tmp_path
    )
    local = service.route(ChatRequest(message="看看本机 CPU 和 Mindspace 服务状态"))
    assert [call.capability for call in local.calls] == [
        "local.system_snapshot",
        "local.mindspace_health",
    ]

    web_disabled = service.route(ChatRequest(message="联网搜索今天的实时热点"))
    assert web_disabled.decision == "direct_answer"
    assert web_disabled.calls == []


def test_contextual_followup_enters_planner_without_broad_single_word_trigger(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
    )
    history = [
        {"role": "user", "content": "最近有什么新的角色音色", "round": 1},
        {"role": "assistant", "content": "刚才提到了一个候选。", "round": 1},
    ]

    strong = service.route(
        ChatRequest(message="除了这个呢，有没有新一点的"),
        history=history,
    )
    weak_with_web_context = service.route(ChatRequest(message="那个呢"), history=history)
    weak_without_context = service.route(
        ChatRequest(message="这个呢"),
        history=[{"role": "assistant", "content": "我喜欢这个配色。", "round": 1}],
    )

    assert strong.decision == "needs_planner"
    assert strong.reason == "contextual_followup"
    assert weak_with_web_context.decision == "needs_planner"
    assert weak_without_context.decision == "direct_answer"


def test_freshness_word_alone_does_not_turn_companion_smalltalk_into_web_work(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
    )

    smalltalk = service.route(ChatRequest(message="你好，今天心情不错。"))
    weather = service.route(ChatRequest(message="今天北京天气怎么样？"))

    assert smalltalk.decision == "direct_answer"
    assert smalltalk.calls == []
    assert [call.capability for call in weather.calls] == ["web.search"]


def test_server_guard_blocks_search_claim_when_no_web_call_succeeded() -> None:
    sanitized, violations = enforce_capability_claims(
        "好，我想想。（搜索了一下网络动态）我刚才在网上查到还有一个更新版本。",
        plan=None,
        results=[],
    )

    assert violations
    assert "搜索了一下" not in sanitized
    assert "网上查到" not in sanitized
    assert "这轮没有实际联网查询" in sanitized

    second, second_violations = enforce_capability_claims(
        "我刚试着帮你搜了下，但好像没抓到靠谱的内容。",
        plan=None,
        results=[],
    )
    assert second_violations
    assert "搜了下" not in second
    assert "这轮没有实际联网查询" in second


def test_six_am_trending_request_is_a_deterministic_web_route(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(
            web_search_enabled=True,
            realtime_topics_enabled=True,
        ),
        runtime_dir=tmp_path,
    )

    plan = service.route(ChatRequest(message="看看互联网上最近有什么有趣的事情。"))

    assert plan.reason == "deterministic_route"
    assert plan.decision == "use_capabilities"
    assert len(plan.calls) == 1
    assert plan.calls[0].capability.startswith("web.")


def test_web_search_is_get_only_public_and_never_json_evidence(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(
            web_search_enabled=True, realtime_topics_enabled=True
        ),
        runtime_dir=tmp_path,
        http_transport=httpx.MockTransport(rss_transport),
    )
    plan = service.route(ChatRequest(message="联网搜索今天的实时热点"))
    results = service.execute(plan, local_snapshot={}, ranked_context=[])

    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].trust == "external_untrusted"
    assert results[0].eligible_for_json_evidence is False
    assert [item["url"] for item in results[0].data["items"]] == [
        "https://example.com/mindspace-news"
    ]


def test_direct_url_is_opened_and_related_original_sources_are_collected(tmp_path):
    service = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        http_transport=httpx.MockTransport(research_transport),
    )
    plan = service.route(ChatRequest(message="https://arxiv.org/abs/2605.14802"))

    assert [call.capability for call in plan.calls] == ["web.open"]
    results = service.execute(plan, local_snapshot={}, ranked_context=[])

    assert results[0].status == "success"
    assert results[0].data["coverage"]["direct_page_opened"] is True
    assert results[0].data["coverage"]["opened_page_count"] == 2
    assert "verified memory architecture" in results[0].data["document"]["content"]
    assert results[0].data["document"]["authors"] == ["Ada Example"]


def test_local_capability_progress_is_not_persisted_as_assistant_reply(tmp_path):
    deps = demo_dependencies()
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(),
        runtime_dir=tmp_path,
        audit=deps.audit,
    )
    request = ChatRequest(message="看看本机 CPU", session_id="single-turn", round=1)
    result = build_graph(deps).invoke({"request": request}, config={"recursion_limit": 30})

    reply = result["response"].reply
    assert not reply.startswith("我先看一下这台电脑现在的状态。")
    stored = deps.sessions.sessions["single-turn"][-1]
    assert stored["content"] == reply
    assistant_messages = [
        item for item in deps.sessions.sessions["single-turn"] if item["role"] == "assistant"
    ]
    assert len(assistant_messages) == 1
    assert deps.profiles.applied_plans == []


class PlannerModel(DeterministicLanguageModel):
    def __init__(self):
        self.planner_calls = 0

    def plan_capabilities(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        self.planner_calls += 1
        return json.dumps(
            {
                "decision": "use_capabilities",
                "reason": "freshness",
                "calls": [
                    {
                        "call_id": "cap_01",
                        "capability": "web.search",
                        "arguments": {"query": "Mindspace 最新消息"},
                    }
                ],
            },
            ensure_ascii=False,
        )


def test_ambiguous_freshness_uses_private_planner_then_one_visible_reply(tmp_path):
    deps = demo_dependencies()
    model = PlannerModel()
    deps.llm = model
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )
    request = ChatRequest(message="听说 Mindspace 有新消息，是真的吗", session_id="planned")
    result = build_graph(deps).invoke({"request": request}, config={"recursion_limit": 30})

    assert model.planner_calls == 1
    assert not result["response"].reply.startswith("我去网上查一下最新信息，等我一下。")
    assert len(deps.sessions.sessions["planned"]) == 2
    assert "【本轮只读观测结果】" in "\n".join(
        message["content"] for message in result["prompt_messages"]
    )


class ContextAwarePlannerModel(DeterministicLanguageModel):
    def __init__(self):
        self.planner_prompt = ""

    def plan_capabilities(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        self.planner_prompt = messages[-1]["content"]
        return json.dumps(
            {
                "capability_plan": {
                    "decision": "use_capabilities",
                    "reason": "freshness",
                    "objective": "查询用户所在城市的天气",
                    "resolved_query": "西安 2026年7月22日 天气预报 降雨",
                    "requires_clarification": False,
                    "clarification_question": "",
                    "follow_up_allowed": True,
                    "calls": [
                        {
                            "call_id": "cap_web_01",
                            "capability": "web.search",
                            "arguments": {"query": "西安 2026年7月22日 天气预报 降雨"},
                        }
                    ],
                }
            },
            ensure_ascii=False,
        )


def test_planner_resolves_elliptical_query_from_recent_conversation(tmp_path):
    deps = demo_dependencies()
    model = ContextAwarePlannerModel()
    deps.llm = model
    deps.sessions.sessions["weather"] = [
        {"role": "user", "content": "帮我查西安今天下不下雨", "round": 1},
        {"role": "assistant", "content": "好，我来查。", "round": 1},
    ]
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )
    request = ChatRequest(message="你先查一下吧", session_id="weather", round=2)

    result = build_graph(deps).invoke({"request": request}, config={"recursion_limit": 30})

    assert "帮我查西安今天下不下雨" in model.planner_prompt
    assert result["capability_plan"].resolved_query == "西安 2026年7月22日 天气预报 降雨"
    assert result["capability_results"][0].data["query"] == "西安 2026年7月22日 天气预报 降雨"


def test_graph_sends_newer_contextual_followup_to_private_planner(tmp_path):
    deps = demo_dependencies()
    model = PlannerModel()
    deps.llm = model
    deps.sessions.sessions["newer-followup"] = [
        {"role": "user", "content": "最近有哪些新的 Mindspace 语音方案", "round": 1},
        {"role": "assistant", "content": "先说一个候选。", "round": 1},
    ]
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )

    result = build_graph(deps).invoke(
        {
            "request": ChatRequest(
                message="除了这个呢，有没有新一点的",
                session_id="newer-followup",
                round=2,
            )
        },
        config={"recursion_limit": 30},
    )

    assert model.planner_calls == 1
    assert result["capability_plan"].decision == "use_capabilities"
    assert result["capability_results"][0].capability == "web.search"


class FailedPlannerModel(DeterministicLanguageModel):
    @staticmethod
    def plan_capabilities(messages: list[dict[str, str]], config: ApiConfig) -> str:
        raise TimeoutError("planner deadline exceeded")


def test_failed_planner_never_searches_raw_elliptical_utterance(tmp_path):
    deps = demo_dependencies()
    deps.llm = FailedPlannerModel()
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )

    result = build_graph(deps).invoke(
        {"request": ChatRequest(message="你先查一下吧", session_id="planner-timeout")},
        config={"recursion_limit": 30},
    )

    assert result["capability_results"] == []
    assert result["capability_plan"].requires_clarification is True
    assert result["capability_plan"].reason == "planner_unavailable"


def test_failed_planner_preserves_an_explicit_deterministic_web_request(tmp_path):
    deps = demo_dependencies()
    deps.llm = FailedPlannerModel()
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(
            web_search_enabled=True,
            realtime_topics_enabled=True,
        ),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )

    result = build_graph(deps).invoke(
        {"request": ChatRequest(message="看看互联网上最近有什么有趣的事情。", session_id="six-am")},
        config={"recursion_limit": 30},
    )

    assert result["capability_plan"].reason == "planner_unavailable"
    assert result["capability_results"]
    assert result["capability_results"][0].status == "success"


class ResearchReviewModel(ContextAwarePlannerModel):
    def __init__(self):
        super().__init__()
        self.review_calls = 0

    def review_research(
        self,
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str:
        self.review_calls += 1
        assert timeout_seconds == 10.0
        return json.dumps(
            {
                "decision": "follow_up",
                "reason": "需要补充官方来源",
                "calls": [
                    {
                        "call_id": "ignored",
                        "capability": "web.search",
                        "arguments": {"query": "西安 气象台 2026年7月22日 降水"},
                    }
                ],
            },
            ensure_ascii=False,
        )


def test_research_review_can_run_one_follow_up_wave_but_keeps_one_reply(tmp_path):
    deps = demo_dependencies()
    model = ResearchReviewModel()
    deps.llm = model
    deps.sessions.sessions["review"] = [
        {"role": "user", "content": "查西安天气", "round": 1},
        {"role": "assistant", "content": "好。", "round": 1},
    ]
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )

    result = build_graph(deps).invoke(
        {"request": ChatRequest(message="你查一下吧", session_id="review", round=2)},
        config={"recursion_limit": 30},
    )

    assert model.review_calls == 1
    assert len(result["capability_results"]) == 2
    assert result["capability_results"][1].call_id == "cap_followup_01"
    assistant_messages = [
        item for item in deps.sessions.sessions["review"] if item["role"] == "assistant"
    ]
    assert len(assistant_messages) == 2


class PlannerThenMalformedModel(PlannerModel):
    def __init__(self):
        super().__init__()
        self.repair_calls = 0

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "<json_update>不是 JSON</json_update>"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        self.repair_calls += 1
        return DeterministicLanguageModel.generate(self, messages, config)


def test_planner_does_not_consume_the_independent_protocol_repair_budget(tmp_path):
    deps = demo_dependencies()
    model = PlannerThenMalformedModel()
    deps.llm = model
    deps.capabilities = ReadOnlyCapabilityService(
        config_provider=lambda: capability_config(web_search_enabled=True),
        runtime_dir=tmp_path,
        audit=deps.audit,
        http_transport=httpx.MockTransport(rss_transport),
    )
    request = ChatRequest(message="听说 LangGraph 有个变化，是真的吗", session_id="two-calls")
    result = build_graph(deps).invoke({"request": request}, config={"recursion_limit": 30})

    assert model.planner_calls == 1
    assert model.repair_calls == 1
    assert result["llm_call_count"] == 3
    assert [item.kind for item in result["response"].model.call_summary] == [
        "planner",
        "generation",
        "protocol_repair",
    ]
    assert result["response"].status == "success"
    assert result["response"].writeback_applied is False
    assert result["response"].reply
