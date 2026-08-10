"""Bounded, read-only capabilities used by the conversational graph.

The registry deliberately exposes observations rather than arbitrary commands.
Permission is persistent and category based: once a category is enabled, calls
inside that category do not require per-call approval.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from mindspace_graph.models import ChatRequest
from mindspace_graph.tool_chain import ToolExecutionResult, ToolInstruction, is_url

DEFAULT_CAPABILITY_SETTINGS: dict[str, Any] = {
    "master_enabled": True,
    "local_knowledge_enabled": True,
    "web_search_enabled": False,
    "realtime_topics_enabled": False,
    "topic_expansion_enabled": True,
    "proactive_hotspots_enabled": False,
    "show_sources_enabled": True,
    "web_timeout_seconds": 12.0,
    "max_web_results": 10,
    "max_web_pages": 6,
    "max_web_content_chars": 12000,
}


_EXPLICIT_WEB_HINTS = re.compile(
    r"(联网|网上|上网|搜索|搜一下|查一下|查查|检索网页|找资料|官网|新闻|热搜|热点)",
    re.IGNORECASE,
)
_ELLIPTICAL_WEB_REQUEST = re.compile(
    r"^(?:你)?(?:先)?(?:帮我)?(?:查|搜|搜索|看看)(?:一?下)?(?:吧|呢|嘛)?[。！？!?]?$",
    re.IGNORECASE,
)
_FRESH_HINTS = re.compile(
    r"(现在|当前|今天|今日|今晚|明天|明早|刚刚|最新|最近|实时|本周|本月|今年|"
    r"去年|上个月|上一代|很久没|旧版|旧价格|价格|版本|发布|更新)",
    re.IGNORECASE,
)
_FRESH_INFORMATION_HINTS = re.compile(
    r"(天气|气温|降雨|下雨|空气质量|台风|地震|汇率|股价|股票|基金|金价|油价|"
    r"价格|报价|官方价|美元|人民币|黄金|比特币|A股|行情|多少钱|赛程|比分|比赛|"
    r"航班|列车|高铁|地铁|日出|政策|法规|法律|规定|版本|稳定版|LTS|升级|落后|过时|"
    r"发布|更新|新闻|热点|热搜|选举|总统|总理|首相|CEO|负责人|时间|几点|"
    r"日期|营业|开放|排名|销量|服务状态|服务端|异常|超时|波动|GitHub|iPhone|Android)",
    re.IGNORECASE,
)
_TREND_HINTS = re.compile(
    r"(热点|热搜|新闻|有什么新鲜事|有趣的事|有意思的事|最近有什么好玩的|聊点什么|近期话题)",
    re.IGNORECASE,
)
_AMBIGUOUS_HINTS = re.compile(
    r"(听说|据说|朋友说|同事说|别人说|有消息说|是不是真的|真的吗|你知道吗|怎么样了|有没有这回事)",
    re.IGNORECASE,
)
_STRONG_CONTEXTUAL_WEB_FOLLOWUP = re.compile(
    r"(除了.{0,12}(?:还有|有没有|别的|其他)|有没有.{0,8}(?:新一点|更新一点|别的|其他)|"
    r"(?:新一点|更新一点|再新一点)|再(?:查|搜|找|看看)|继续(?:查|搜|找)|"
    r"接着(?:查|搜|找)|还有(?:什么)?(?:新的|更新的|别的|其他))",
    re.IGNORECASE,
)
_WEAK_CONTEXTUAL_FOLLOWUP = re.compile(
    r"(这个|那个|这些|那些|除了这个|除了那个|还有呢|别的呢|另外呢|然后呢|继续呢)",
    re.IGNORECASE,
)
_RECENT_WEB_CONTEXT = re.compile(
    r"(联网|网上|网络|网页|官网|搜索|查询|检索|来源|链接|新闻|热点|最新|最近|实时|发布|更新)",
    re.IGNORECASE,
)
_KNOWLEDGE_HINTS = re.compile(r"(知识库|资料库|你(?:还)?记得|你还记不记得|回忆一下|我们以前|档案里)")
_HISTORY_HINTS = re.compile(
    r"(之前.{0,8}说过|前面.{0,8}说过|刚才.{0,8}说过|我们聊过|记录里|聊天记录|上次)"
)
_EXTERNAL_SUBJECT_HINTS = re.compile(
    r"(DeepSeek|Gemma|Python|API|模型|软件|版本|官网|天气|新闻|价格|政策|法规|比赛|电影|游戏)",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
_PARENTHETICAL_UNVERIFIED_WEB_ACTION = re.compile(
    r"[（(][^（）()]{0,12}(?:搜索|查询|检索|上网|联网)[^（）()]{0,24}[）)]"
)
_UNVERIFIED_WEB_ACTION = re.compile(
    r"(?:(?:我|我这边|刚才|刚刚|这边)[^。！？!?\n]{0,12}"
    r"(?:上网|联网|网上|网络|网页|官网|搜索|查询|检索|搜(?:了)?(?:一)?下|搜到|查到)"
    r"[^。！？!?\n]{0,28}(?:了|到|显示|发现|结果|信息|动态|资料)|"
    r"(?:根据|从)(?:网上|官网|网页|搜索结果)[^。！？!?\n]{0,12}(?:显示|来看|得知))",
    re.IGNORECASE,
)


class _ReadableHTMLParser(HTMLParser):
    """Extract readable page text and citation metadata without executing markup."""

    _IGNORED = {"script", "style", "svg", "noscript", "template", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.metadata: dict[str, list[str]] = {}
        self._ignored_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._IGNORED:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag != "meta":
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        name = (values.get("name") or values.get("property") or "").lower()
        content = unescape(values.get("content") or "").strip()
        if name and content:
            self.metadata.setdefault(name, []).append(content)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._title_depth:
            self.title_parts.append(value)
        self.parts.append(value)


class ReadOnlyCapabilityService:
    """Resolve and execute a small allow-listed set of read-only observations."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], dict[str, Any]],
        runtime_dir: Path,
        audit: Any | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config_provider = config_provider
        self.runtime_dir = runtime_dir
        self.audit = audit
        self.http_transport = http_transport
        self._http = httpx.Client(
            timeout=httpx.Timeout(12.0, connect=5.0),
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            follow_redirects=False,
            transport=http_transport,
        )

    def close(self) -> None:
        self._http.close()

    def settings(self) -> dict[str, Any]:
        raw = self._config_provider().get("capabilities", {})
        value = dict(DEFAULT_CAPABILITY_SETTINGS)
        if isinstance(raw, dict):
            value.update({key: raw[key] for key in value if key in raw})
        return value

    def enabled(self, key: str) -> bool:
        settings = self.settings()
        return bool(settings["master_enabled"] and settings.get(key, False))

    def route_hint(self, request: ChatRequest, *, history: list[dict[str, Any]] | None = None) -> str:
        """Return a lexical hint only. It never executes or asks another model."""

        del history
        message = request.message.strip()
        if _URL_PATTERN.search(message) or _EXPLICIT_WEB_HINTS.search(message) or (
            _FRESH_HINTS.search(message) and _FRESH_INFORMATION_HINTS.search(message)
        ):
            return "web"
        if re.search(r"(任务|待办|提醒|截止|完成.{0,8}(?:任务|待办))", message):
            return "task"
        if _AMBIGUOUS_HINTS.search(message) and _EXTERNAL_SUBJECT_HINTS.search(message):
            return "web"
        if _KNOWLEDGE_HINTS.search(message) or _HISTORY_HINTS.search(message):
            return "memory"
        return ""

    def execute_web(self, instruction: ToolInstruction) -> ToolExecutionResult:
        started = datetime.now(UTC)
        try:
            raw = self._web_open(instruction.parameter) if is_url(instruction.parameter) else self._web_search(
                instruction.parameter,
                page_budget=5,
            )
            sources: list[dict[str, Any]] = []
            used_chars = 0
            documents = list(raw.get("documents") or [])
            if isinstance(raw.get("document"), dict):
                documents.insert(0, raw["document"])
            for document in documents:
                if not isinstance(document, dict) or document.get("status") != "success":
                    continue
                content = str(document.get("content") or "")
                remaining = max(0, 8000 - used_chars)
                if sources and remaining <= 0:
                    break
                clipped = content[: min(1600, remaining)]
                sources.append(
                    {
                        "title": str(document.get("title") or "")[:300],
                        "url": str(document.get("url") or "")[:2000],
                        "source": str(document.get("source") or "")[:200],
                        "published_at": str(document.get("published_at") or "")[:100],
                        "content": clipped,
                    }
                )
                used_chars += len(clipped)
                if len(sources) >= 5:
                    break
            if not sources:
                for item in list(raw.get("items") or [])[:5]:
                    summary = str(item.get("summary") or "")[:1200]
                    sources.append(
                        {
                            "title": str(item.get("title") or "")[:300],
                            "url": str(item.get("url") or "")[:2000],
                            "source": str(item.get("source") or "")[:200],
                            "published_at": str(item.get("published_at") or "")[:100],
                            "content": summary,
                        }
                    )
            elapsed = (datetime.now(UTC) - started).total_seconds() * 1000
            result = ToolExecutionResult(
                call_id=instruction.call_id,
                tool="web",
                level=3,
                status="success",
                parameter_summary=instruction.parameter_summary,
                elapsed_ms=round(elapsed, 1),
                source_count=len(sources),
                data={"query": instruction.parameter, "sources": sources, "count": len(sources)},
            )
        except Exception as exc:
            elapsed = (datetime.now(UTC) - started).total_seconds() * 1000
            result = ToolExecutionResult(
                call_id=instruction.call_id,
                tool="web",
                level=3,
                status="failed",
                parameter_summary=instruction.parameter_summary,
                elapsed_ms=round(elapsed, 1),
                error=str(exc)[:500],
            )
        if self.audit is not None:
            self.audit.record(
                "tool_web_executed",
                result.model_dump(mode="json", exclude={"data"}),
            )
        return result
    def _web_search(
        self,
        query: str,
        *,
        exclude_urls: set[str] | None = None,
        page_budget: int | None = None,
    ) -> dict[str, Any]:
        """Search broadly, then open original sources before the answer is generated."""

        if not query:
            raise ValueError("web query is blank")
        settings = self.settings()
        timeout = max(2.0, min(30.0, float(settings["web_timeout_seconds"])))
        limit = max(1, min(20, int(settings["max_web_results"])))
        pages = max(0, min(10, int(settings["max_web_pages"])))
        if page_budget is not None:
            pages = max(0, min(pages, int(page_budget)))
        search_url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
        headers = {
            "User-Agent": "Mindspace/0.5 read-only-research (+https://douyinqijun.cn)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        excluded = {self._canonical_url(item) for item in (exclude_urls or set())}
        documents: list[dict[str, Any]] = []
        page_errors: list[dict[str, str]] = []
        response = self._get_public(
            self._http,
            search_url,
            headers=headers,
            max_bytes=2 * 1024 * 1024,
            timeout=timeout,
        )
        root = ElementTree.fromstring(response.content)
        items: list[dict[str, str]] = []
        for node in root.findall(".//item"):
            link = (node.findtext("link") or "").strip()
            if not self._public_http_url(link):
                continue
            title = self._plain_text(node.findtext("title") or "")[:300]
            description = self._plain_text(node.findtext("description") or "")[:1500]
            published = (node.findtext("pubDate") or "").strip()
            items.append(
                {
                    "title": title,
                    "summary": description,
                    "url": link,
                    "source": urlparse(link).hostname or "",
                    "published_at": published,
                }
            )
            if len(items) >= limit:
                break
        if not items:
            raise ValueError("search returned no public results")
        page_urls: list[str] = []
        for item in items:
            if len(page_urls) >= pages:
                break
            canonical = self._canonical_url(item["url"])
            if canonical in excluded:
                continue
            excluded.add(canonical)
            page_urls.append(item["url"])

        def fetch_page(url: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
            try:
                return self._fetch_document(self._http, url), None
            except Exception as exc:  # noqa: BLE001 - one blocked page must not discard the search
                return None, {"url": url, "error": str(exc)[:300]}

        if page_urls:
            with ThreadPoolExecutor(max_workers=min(4, len(page_urls)), thread_name_prefix="web-page") as executor:
                for document, error in executor.map(fetch_page, page_urls):
                    if document is not None:
                        documents.append(document)
                    if error is not None:
                        page_errors.append(error)
        successful = [item for item in documents if item.get("status") == "success"]
        domains = sorted({str(item.get("source") or "") for item in successful if str(item.get("source") or "")})
        return {
            "query": query,
            "engine": "bing-rss",
            "fetched_at": datetime.now(UTC).isoformat(),
            "items": items,
            "documents": documents,
            "page_errors": page_errors,
            "coverage": {
                "search_result_count": len(items),
                "opened_page_count": len(successful),
                "source_domain_count": len(domains),
                "source_domains": domains,
                "search_snippets_are_evidence": False,
            },
        }

    def _web_open(self, url: str) -> dict[str, Any]:
        """Open the supplied URL first, then search its title for corroborating sources."""

        if not self._public_http_url(url):
            raise ValueError("web URL must be a public HTTP(S) address")
        settings = self.settings()
        readable_url = self._canonical_readable_url(url)
        document = self._fetch_document(self._http, readable_url)
        title = str(document.get("title") or "").strip()
        related: dict[str, Any] = {
            "items": [],
            "documents": [],
            "page_errors": [],
            "coverage": {
                "search_result_count": 0,
                "opened_page_count": 0,
                "source_domain_count": 0,
                "source_domains": [],
                "search_snippets_are_evidence": False,
            },
        }
        if title:
            try:
                related = self._web_search(
                    title[:240],
                    exclude_urls={readable_url, str(document.get("url") or "")},
                    page_budget=max(0, int(settings["max_web_pages"]) - 1),
                )
            except Exception as exc:  # noqa: BLE001 - the supplied page remains usable
                related["page_errors"] = [{"url": "related_search", "error": str(exc)[:300]}]
        related_documents = list(related.get("documents") or [])
        all_documents = [document, *related_documents]
        successful = [item for item in all_documents if item.get("status") == "success"]
        domains = sorted({str(item.get("source") or "") for item in successful if str(item.get("source") or "")})
        return {
            "requested_url": url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "document": document,
            "related_query": title,
            "items": related.get("items") or [],
            "documents": all_documents,
            "page_errors": related.get("page_errors") or [],
            "coverage": {
                "direct_page_opened": document.get("status") == "success",
                "search_result_count": len(related.get("items") or []),
                "opened_page_count": len(successful),
                "source_domain_count": len(domains),
                "source_domains": domains,
                "search_snippets_are_evidence": False,
            },
        }

    def _fetch_document(self, client: httpx.Client, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": "Mindspace/0.5 read-only-research (+https://douyinqijun.cn)",
            "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.2",
        }
        response = self._get_public(client, url, headers=headers, max_bytes=4 * 1024 * 1024)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        max_chars = max(2000, min(30000, int(self.settings()["max_web_content_chars"])))
        final_url = str(response.url)
        source = urlparse(final_url).hostname or ""
        if content_type in {"text/html", "application/xhtml+xml", ""}:
            parser = _ReadableHTMLParser()
            parser.feed(response.text)
            meta = parser.metadata
            title = self._first_meta(meta, "citation_title", "og:title")
            title = title or self._plain_text(" ".join(parser.title_parts))
            description = self._first_meta(
                meta,
                "citation_abstract",
                "description",
                "og:description",
            )
            authors = [self._plain_text(value)[:200] for value in meta.get("citation_author", [])[:30]]
            published = self._first_meta(meta, "citation_date", "article:published_time", "date")
            body = self._plain_text(" ".join(parser.parts))
            if description and description not in body[:max_chars]:
                body = f"{description}\n\n{body}"
            return {
                "status": "success",
                "url": final_url,
                "source": source,
                "content_type": content_type or "text/html",
                "title": title[:500],
                "description": description[:2000],
                "authors": authors,
                "published_at": published[:100],
                "content": body[:max_chars],
                "content_chars": min(len(body), max_chars),
                "truncated": len(body) > max_chars,
            }
        if content_type.startswith("text/") or content_type in {
            "application/json",
            "application/xml",
            "application/rss+xml",
        }:
            body = self._plain_text(response.text)
            return {
                "status": "success",
                "url": final_url,
                "source": source,
                "content_type": content_type,
                "title": "",
                "description": "",
                "authors": [],
                "published_at": "",
                "content": body[:max_chars],
                "content_chars": min(len(body), max_chars),
                "truncated": len(body) > max_chars,
            }
        raise ValueError(f"unsupported page content type: {content_type or 'unknown'}")

    def _get_public(
        self,
        client: httpx.Client,
        url: str,
        *,
        headers: dict[str, str],
        max_bytes: int,
        timeout: float | None = None,
    ) -> httpx.Response:
        current = url
        for _ in range(6):
            self._ensure_public_destination(current)
            response = client.get(
                current,
                headers=headers,
                timeout=timeout or float(self.settings()["web_timeout_seconds"]),
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            if len(response.content) > max_bytes:
                raise ValueError(f"web response exceeds {max_bytes // 1024 // 1024} MiB")
            return response
        raise ValueError("too many web redirects")

    def _ensure_public_destination(self, value: str) -> None:
        if not self._public_http_url(value):
            raise ValueError("web destination is not public")
        if self.http_transport is not None:
            return
        parsed = urlparse(value)
        try:
            addresses = socket.getaddrinfo(
                parsed.hostname,
                parsed.port or 443,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError(f"cannot resolve web destination: {exc}") from exc
        for item in addresses:
            address = ipaddress.ip_address(item[4][0])
            # TUN/system-proxy clients commonly synthesize public DNS answers in
            # 198.18.0.0/15.  The hostname itself is still public and httpx will
            # send it through the configured proxy, so do not mistake that
            # benchmark address for an SSRF attempt.
            if address in ipaddress.ip_network("198.18.0.0/15"):
                continue
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                raise ValueError("web destination resolved to a non-public address")

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for match in _URL_PATTERN.findall(text):
            value = match.rstrip(".,!?;:，。！？；：、)）}")
            canonical = ReadOnlyCapabilityService._canonical_url(value)
            if canonical in seen or not ReadOnlyCapabilityService._public_http_url(value):
                continue
            seen.add(canonical)
            values.append(value)
        return values

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        return parsed._replace(fragment="").geturl().rstrip("/")

    @staticmethod
    def _canonical_readable_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"} and parsed.path.startswith("/pdf/"):
            paper_id = parsed.path.removeprefix("/pdf/").removesuffix(".pdf")
            return parsed._replace(path=f"/abs/{paper_id}", query="", fragment="").geturl()
        return value

    @staticmethod
    def _first_meta(metadata: dict[str, list[str]], *names: str) -> str:
        for name in names:
            values = metadata.get(name, [])
            if values:
                return ReadOnlyCapabilityService._plain_text(values[0])
        return ""

    @staticmethod
    def _plain_text(value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _public_http_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return False
            host = parsed.hostname.lower()
            if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
                return False
            try:
                address = ipaddress.ip_address(host)
                return not (
                    address.is_private
                    or address.is_loopback
                    or address.is_link_local
                    or address.is_reserved
                    or address.is_multicast
                    or address.is_unspecified
                )
            except ValueError:
                return True
        except ValueError:
            return False



