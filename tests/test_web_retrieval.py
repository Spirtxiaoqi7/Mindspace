from __future__ import annotations

from mindspace_graph.models import ChatRequest
from mindspace_graph.event_memory import event_memory_lane
from mindspace_graph.provider_capabilities import (
    ProviderCapabilityRegistry,
    ProviderCapabilityState,
    explicitly_rejects_tools,
)
from mindspace_graph.web.models import WebQuery, WebSource
from mindspace_graph.web.ranking import rank_sources
from mindspace_graph.web.routing import decide_retrieval
from mindspace_graph.web.providers import SocialProvider


def test_routing_force_allow_and_roleplay_suppression() -> None:
    assert decide_retrieval(ChatRequest(message="帮我联网搜索 OpenAI 最新发布", session_id="routing")).mode == "force"
    assert decide_retrieval(ChatRequest(message="OpenAI 今天发布了什么", session_id="routing")).mode == "force"
    assert decide_retrieval(ChatRequest(message="我想知道你现在在想什么", session_id="routing")).mode == "suppress"
    assert decide_retrieval(ChatRequest(message="那最新的呢", session_id="routing"), [{"role": "user", "content": "联网搜索 OpenAI 最新发布"}]).mode == "force"
    assert decide_retrieval(ChatRequest(message="帮我在 GitHub 查找 LangGraph 官方仓库最近发布版本和更新时间。", session_id="routing")).mode == "force"


def test_routing_inherits_weather_and_social_topics_for_entity_followups() -> None:
    weather_history = [
        {"role": "user", "content": "今天天气怎么样"},
        {"role": "assistant", "content": "我查到了今天的天气。"},
    ]
    weather = decide_retrieval(
        ChatRequest(message="西安的呢？", session_id="weather-followup"),
        weather_history,
    )
    assert weather.mode == "force"
    assert weather.query == "西安 今天 天气预报"
    assert "weather_followup" in weather.reason_codes

    social_history = [
        {"role": "user", "content": "最近OpenAI发了什么推文？"},
        {"role": "assistant", "content": "我找到了 OpenAI 最近发布的推文。"},
    ]
    social = decide_retrieval(
        ChatRequest(message="山姆奥特曼呢？", session_id="social-followup"),
        social_history,
    )
    assert social.mode == "force"
    assert social.platforms == ["x"]
    assert social.scope == "social"
    assert "山姆奥特曼" in social.query


def test_entity_followup_does_not_inherit_non_external_roleplay() -> None:
    decision = decide_retrieval(
        ChatRequest(message="西安的呢？", session_id="roleplay-followup"),
        [
            {"role": "user", "content": "你喜欢上海吗？"},
            {"role": "assistant", "content": "喜欢，因为那里有你。"},
        ],
    )
    assert decision.mode == "suppress"


def test_social_external_request_wins_over_event_memory_lane() -> None:
    for message in (
        "最近OpenAI发了什么推文？",
        "Reddit最近讨论什么？",
        "B站最近有什么视频？",
        "GitHub上Mindspace最近更新了什么？",
        "抖音最近有什么动态？",
    ):
        assert event_memory_lane(message) is False


def test_platform_followup_keeps_the_active_platform() -> None:
    decision = decide_retrieval(
        ChatRequest(message="Mindspace呢？", session_id="github-followup"),
        [{"role": "user", "content": "GitHub上LangGraph最近更新了什么？"}],
    )
    assert decision.mode == "force"
    assert decision.platforms == ["github"]
    assert decision.scope == "developer"


def test_known_x_handles_are_resolved_without_guessing_unknown_people() -> None:
    assert SocialProvider._x_handle("最近OpenAI发了什么推文？") == "OpenAI"
    assert SocialProvider._x_handle("山姆奥特曼呢？") == "sama"
    assert SocialProvider._x_handle("某个陌生人最近发了什么") == ""


def test_only_explicit_unsupported_field_persists_tool_downgrade() -> None:
    registry = ProviderCapabilityRegistry()
    registry.unsupported("https://provider.example/v1", "chat", "unsupported_field")
    assert registry.get("https://provider.example/v1", "chat").state == ProviderCapabilityState.UNSUPPORTED
    registry.transient_failure("https://other.example/v1", "chat", "http_401")
    assert registry.get("https://other.example/v1", "chat").state == ProviderCapabilityState.TRANSIENT_FAILURE
    assert explicitly_rejects_tools(400, "Thinking mode does not support this tool_choice") is False
    assert explicitly_rejects_tools(400, "tools are not supported") is True


def test_ranking_keeps_diverse_relevant_sources() -> None:
    ranked = rank_sources(WebQuery(query="Mindspace release"), [WebSource(source_type="index", url="https://example.com/release", title="Mindspace release", evidence_level="indexed"), WebSource(source_type="index", url="https://example.com/tag/release", title="Mindspace release", evidence_level="indexed"), WebSource(source_type="api", url="https://github.com/org/repo/releases", title="Mindspace release", official_account=True, evidence_level="full")], limit=2)
    assert len(ranked) == 2
    assert ranked[0].official_account
