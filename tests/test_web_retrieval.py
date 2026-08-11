from __future__ import annotations

from mindspace_graph.models import ChatRequest
from mindspace_graph.provider_capabilities import ProviderCapabilityRegistry, ProviderCapabilityState
from mindspace_graph.web.models import WebQuery, WebSource
from mindspace_graph.web.ranking import rank_sources
from mindspace_graph.web.routing import decide_retrieval


def test_routing_force_allow_and_roleplay_suppression() -> None:
    assert decide_retrieval(ChatRequest(message="帮我联网搜索 OpenAI 最新发布", session_id="routing")).mode == "force"
    assert decide_retrieval(ChatRequest(message="OpenAI 今天发布了什么", session_id="routing")).mode == "force"
    assert decide_retrieval(ChatRequest(message="我想知道你现在在想什么", session_id="routing")).mode == "suppress"
    assert decide_retrieval(ChatRequest(message="那最新的呢", session_id="routing"), [{"role": "user", "content": "联网搜索 OpenAI 最新发布"}]).mode == "force"
    assert decide_retrieval(ChatRequest(message="帮我在 GitHub 查找 LangGraph 官方仓库最近发布版本和更新时间。", session_id="routing")).mode == "force"


def test_only_explicit_unsupported_field_persists_tool_downgrade() -> None:
    registry = ProviderCapabilityRegistry()
    registry.unsupported("https://provider.example/v1", "chat", "unsupported_field")
    assert registry.get("https://provider.example/v1", "chat").state == ProviderCapabilityState.UNSUPPORTED
    registry.transient_failure("https://other.example/v1", "chat", "http_401")
    assert registry.get("https://other.example/v1", "chat").state == ProviderCapabilityState.TRANSIENT_FAILURE


def test_ranking_keeps_diverse_relevant_sources() -> None:
    ranked = rank_sources(WebQuery(query="Mindspace release"), [WebSource(source_type="index", url="https://example.com/release", title="Mindspace release", evidence_level="indexed"), WebSource(source_type="index", url="https://example.com/tag/release", title="Mindspace release", evidence_level="indexed"), WebSource(source_type="api", url="https://github.com/org/repo/releases", title="Mindspace release", official_account=True, evidence_level="full")], limit=2)
    assert len(ranked) == 2
    assert ranked[0].official_account
