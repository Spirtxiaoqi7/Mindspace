from __future__ import annotations

import httpx

from mindspace_graph.web.models import WebQuery, WebSource
from mindspace_graph.web.orchestrator import WebOrchestrator
from mindspace_graph.web.policy import finalize_result
from mindspace_graph.web.providers import SearchProvider
from mindspace_graph.web.readers import PublicHttpClient


def test_platform_failures_are_isolated_and_indexed_evidence_is_honest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "search.brave.com":
            return httpx.Response(200, text='<div data-type="web"><a class="l1" href="https://x.com/a/status/1"><div class="search-snippet-title">indexed X post</div></a><div class="generic-snippet"><div class="content">indexed summary</div></div>')
        if host == "www.bing.com":
            return httpx.Response(200, text="<rss><channel /></rss>")
        if host == "api.github.com":
            if path.startswith("/repos/org/repo/releases/latest"):
                return httpx.Response(200, json={"html_url": "https://github.com/org/repo/releases/tag/v1.0", "tag_name": "v1.0", "published_at": "2026-08-10T00:00:00Z", "author": {"login": "org"}})
            return httpx.Response(200, json={"items": [{"html_url": "https://github.com/org/repo", "full_name": "org/repo", "description": "release", "owner": {"login": "org"}}]})
        if host == "api.bilibili.com":
            return httpx.Response(200, json={"data": {"result": [{"arcurl": "https://www.bilibili.com/video/BV1", "title": "public", "description": "summary", "author": "creator"}]}})
        if host in {"public.api.bsky.app", "mastodon.social", "www.reddit.com"}:
            return httpx.Response(403 if host != "mastodon.social" else 401, text="public endpoint unavailable")
        if host == "publish.twitter.com":
            return httpx.Response(404, text="not found")
        if host == "x.com":
            return httpx.Response(200, text="<title>indexed page</title><body>body</body>")
        raise AssertionError(f"unexpected request: {request.url}")

    web = WebOrchestrator(config_provider=lambda: {"capabilities": {"max_web_pages": 0, "max_web_results": 8}}, http_transport=httpx.MockTransport(handler))
    result = web.execute(WebQuery(query="release", platforms=["github", "bilibili", "bluesky", "mastodon", "reddit", "x"]))
    assert result.status == "success"
    assert result.coverage == "partial"
    assert all(item.platform == "github" for item in result.sources if "github.com" in item.url)
    assert {failure["provider"] for failure in result.failures} >= {"bluesky", "mastodon", "reddit"}
    x_values, _provider = web.social.search(WebQuery(query="Mindspace release"), "x")
    web.close()
    x_source = next(item for item in x_values if item.platform == "x")
    assert x_source.evidence_level == "indexed_summary"


def test_platform_coverage_requires_target_evidence_and_strict_queries_need_timestamps() -> None:
    web_only = WebSource(url="https://example.com/x", title="X discussion", platform="web", freshness="search_index")
    x_result = finalize_result(WebQuery(query="X posts", platforms=["x"]), [web_only], [])
    assert x_result.coverage == "unavailable"
    assert x_result.sources == []

    x_index = WebSource(url="https://x.com/account/status/123", title="post", platform="x", freshness="search_index", evidence_level="indexed_summary")
    assert finalize_result(WebQuery(query="X posts", platforms=["x"]), [x_index], []).coverage == "indexed"
    x_home = WebSource(url="https://x.com/OpenAI", title="OpenAI", platform="x", freshness="search_index", evidence_level="indexed_summary")
    assert finalize_result(WebQuery(query="X posts", platforms=["x"]), [x_home], []).coverage == "unavailable"

    xia_home = WebSource(url="https://www.xiaohongshu.com/", title="小红书", platform="xiaohongshu", freshness="search_index")
    xia_note = WebSource(url="https://www.xiaohongshu.com/explore/abc123", title="具体笔记", platform="xiaohongshu", freshness="search_index")
    assert finalize_result(WebQuery(query="小红书帖子", platforms=["xiaohongshu"]), [xia_home], []).coverage == "unavailable"
    assert finalize_result(WebQuery(query="小红书帖子", platforms=["xiaohongshu"]), [xia_note], []).coverage == "indexed"

    official_without_date = WebSource(url="https://example.com/release", title="official", platform="web", freshness="official_api", official_account=True)
    assert finalize_result(WebQuery(query="官方最近一周", scope="official", recency="week"), [official_without_date], []).coverage == "partial"


def test_official_brand_filter_rejects_lookalikes_and_marks_only_canonical_domains_authoritative() -> None:
    canonical = WebSource(url="https://api.deepseek.com/news", title="DeepSeek official", platform="web", published_at="2026-08-10T00:00:00Z", freshness="official_api")
    mirrors = [
        WebSource(url="https://deep-seek.com", title="mirror", platform="web", freshness="search_index"),
        WebSource(url="https://desktop-deepseek.example", title="desktop", platform="web", freshness="search_index"),
        WebSource(url="https://deepseeka.com", title="lookalike", platform="web", freshness="search_index"),
        WebSource(url="https://news.example.com/deepseek", title="secondary", platform="web", freshness="search_index", official_account=True),
    ]
    result = finalize_result(WebQuery(query="DeepSeek 官方最近一周", scope="official", recency="week"), [canonical, *mirrors], [])
    assert result.coverage == "complete"
    assert {item.url for item in result.sources}.isdisjoint({item.url for item in mirrors[:3]})
    official = next(item for item in result.sources if item.url == canonical.url)
    secondary = next(item for item in result.sources if item.url == mirrors[3].url)
    assert (official.official_account, official.authority) == (True, "official_domain")
    assert (secondary.official_account, secondary.authority) == (False, "secondary")


def test_brave_rate_limit_enters_cooldown_and_uses_bing_without_repeated_failure() -> None:
    calls = {"brave": 0, "bing": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.brave.com":
            calls["brave"] += 1
            return httpx.Response(429, text="rate limited")
        if request.url.host == "www.bing.com":
            calls["bing"] += 1
            return httpx.Response(200, text='<li class="b_algo"><h2><a href="https://example.com/item">fallback</a></h2><p>summary</p></li>')
        raise AssertionError(request.url)

    SearchProvider._brave_cooldown_until = 0.0
    search = SearchProvider(WebOrchestrator(config_provider=lambda: {}, http_transport=httpx.MockTransport(handler)).http, lambda: {})
    try:
        first, first_failures, _ = search.search(WebQuery(query="first"))
        second, second_failures, _ = search.search(WebQuery(query="second"))
    finally:
        search.http.close()
        SearchProvider._brave_cooldown_until = 0.0
    assert first and second
    assert calls == {"brave": 1, "bing": 2}
    assert first_failures == [{"provider": "brave_html", "error": "rate limited; temporarily skipped"}]
    assert second_failures == []


def test_natural_language_github_query_resolves_langgraph_and_reads_latest_release() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/repos/langchain-ai/langgraph/releases/latest":
            return httpx.Response(200, json={"html_url": "https://github.com/langchain-ai/langgraph/releases/tag/1.2.3", "tag_name": "1.2.3", "published_at": "2026-08-10T00:00:00Z", "author": {"login": "langchain-ai"}})
        raise AssertionError(request.url)

    web = WebOrchestrator(config_provider=lambda: {"capabilities": {"max_web_results": 8}}, http_transport=httpx.MockTransport(handler))
    result = web.execute(WebQuery(query="帮我在 GitHub 查找 LangGraph 官方仓库最近发布版本和更新时间。", platforms=["github"], scope="developer", recency="week"))
    web.close()
    assert requests == ["/repos/langchain-ai/langgraph/releases/latest"]
    assert result.coverage == "complete"
    assert [(item.platform, item.url) for item in result.sources] == [("github", "https://github.com/langchain-ai/langgraph/releases/tag/1.2.3")]


def test_bing_html_parser_is_used_before_rss_when_brave_is_unavailable() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "search.brave.com":
            return httpx.Response(503, text="unavailable")
        if request.url.host == "www.bing.com" and request.url.params.get("format") is None:
            return httpx.Response(200, text='<li class="b_algo"><h2><a href="https://github.com/langchain-ai/langgraph">LangGraph</a></h2><p>Stateful graph framework</p></li>')
        raise AssertionError(request.url)

    SearchProvider._brave_cooldown_until = 0.0
    http = PublicHttpClient(transport=httpx.MockTransport(handler))
    try:
        values, failures, providers = SearchProvider(http, lambda: {}).search(WebQuery(query="LangGraph"))
    finally:
        http.close()
        SearchProvider._brave_cooldown_until = 0.0
    assert calls == ["search.brave.com", "www.bing.com"]
    assert providers == ["bing_html"]
    assert values[0].url == "https://github.com/langchain-ai/langgraph"
    assert failures == [{"provider": "brave_html", "error": "HTTP 503"}]


def test_bing_click_wrapper_is_html_unescaped_and_decoded_before_policy() -> None:
    encoded = "a1aHR0cHM6Ly9vcGVuYWkuY29tL25ld3M="
    raw = f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?x=1&amp;u={encoded}">OpenAI news</a></h2><p>OpenAI update</p></li>'
    values = SearchProvider._bing_links(raw)
    assert [(item.url, item.title) for item in values] == [("https://openai.com/news", "OpenAI news")]


def test_filtered_zero_results_are_successful_but_total_provider_failure_is_not() -> None:
    empty = finalize_result(WebQuery(query="DeepSeek 官方最近一周", scope="official", recency="week"), [], [], executed=True)
    failed = finalize_result(WebQuery(query="DeepSeek 官方最近一周", scope="official", recency="week"), [], [{"provider": "bing_html", "error": "HTTP 503"}], executed=False)
    assert (empty.status, empty.coverage, empty.reason_codes) == ("success", "unavailable", ["no_relevant_results"])
    assert (failed.status, failed.coverage, failed.reason_codes) == ("failed", "unavailable", ["providers_failed"])


def test_real_input_fixtures_drop_irrelevant_web_results_before_coverage() -> None:
    x_result = finalize_result(
        WebQuery(query="帮我在 X 查找 OpenAI 官方账号最近动态", platforms=["x"]),
        [
            WebSource(url="https://support.microsoft.com/help", title="Microsoft support", text="Windows help", platform="web"),
            WebSource(url="https://x.com/OpenAI", title="OpenAI", text="Official profile", platform="x", evidence_level="indexed_summary"),
        ],
        [],
    )
    assert x_result.coverage == "unavailable"
    assert [(item.platform, item.url) for item in x_result.sources] == [("x", "https://x.com/OpenAI")]

    deepseek_result = finalize_result(
        WebQuery(query="请联网搜索 DeepSeek 官方最近一周发布", scope="official", recency="week"),
        [WebSource(url="https://support.google.com/help", title="Google support", text="Account help", platform="web")],
        [],
    )
    assert deepseek_result.coverage == "unavailable"
    assert deepseek_result.sources == []
