from __future__ import annotations

import html
import json
import re
import time
from threading import Lock
from base64 import urlsafe_b64decode
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from .models import WebQuery, WebSource
from .readers import PublicHttpClient
from .relevance import core_entities


def compact_failure(provider: str, error: Exception | str) -> dict[str, str]:
    if isinstance(error, httpx.HTTPStatusError):
        return {"provider": provider, "error": f"HTTP {error.response.status_code}"}
    if isinstance(error, httpx.TimeoutException):
        return {"provider": provider, "error": "timeout"}
    return {"provider": provider, "error": "request failed"}


def _clean(value: str, limit: int = 1800) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()[:limit]


class SearchProvider:
    _brave_cooldown_until = 0.0
    _brave_lock = Lock()
    _brave_cooldown_seconds = 120.0

    def __init__(self, http, config_provider):
        self.http, self.config = http, config_provider

    def search(self, query):
        failures: list[dict[str, str]] = []
        successful: list[str] = []
        try:
            configured = self._configured_search_api(query)
            if configured is not None:
                successful.append("search_api")
                values = self._json_results(configured)
                if values:
                    return values, failures, successful
        except Exception as exc:
            failures.append(compact_failure("search_api", exc))
        candidates = [
            ("brave_html", f"https://search.brave.com/search?q={quote_plus(query.query)}", self._brave_links),
            ("bing_html", f"https://www.bing.com/search?q={quote_plus(query.query)}", self._bing_links),
            ("baidu_html", f"https://www.baidu.com/s?wd={quote_plus(query.query)}", self._baidu_links),
            ("ddg_html", f"https://html.duckduckgo.com/html/?q={quote_plus(query.query)}", self._ddg_links),
            ("bing_rss", f"https://www.bing.com/search?format=rss&q={quote_plus(query.query)}", self._rss_links),
        ]
        for name, url, parser in candidates:
            if name == "brave_html" and self._brave_in_cooldown():
                continue
            try:
                response = self.http.get(url)
                if name == "brave_html" and response.status_code == 429:
                    self._trip_brave_cooldown()
                    failures.append({"provider": "brave_html", "error": "rate limited; temporarily skipped"})
                    continue
                response.raise_for_status()
                successful.append(name)
                values = parser(response.text)
                if values:
                    return values, failures, successful
            except Exception as exc:
                failures.append(compact_failure(name, exc))
        return [], failures, successful

    def _configured_search_api(self, query: WebQuery):
        config = self.config() if callable(self.config) else {}
        source = config.get("search_api") if isinstance(config, dict) else None
        if not isinstance(source, dict) or not str(source.get("url") or "").startswith("https://"):
            return None
        headers = {str(key): str(value) for key, value in (source.get("headers") or {}).items() if value}
        response = self.http.get(str(source["url"]), params={str(source.get("query_param") or "q"): query.query}, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _json_results(payload) -> list[WebSource]:
        if not isinstance(payload, dict):
            return []
        rows = payload.get("results") or (payload.get("web") or {}).get("results") or payload.get("data") or []
        return [WebSource(source_type="api", url=str(item.get("url") or ""), title=str(item.get("title") or ""), text=str(item.get("description") or item.get("snippet") or ""), freshness="search_index", evidence_level="search_api") for item in rows if isinstance(item, dict) and item.get("url")]

    @staticmethod
    def _brave_links(raw: str) -> list[WebSource]:
        values = []
        for match in re.finditer(r'<a\b[^>]*\bhref="(https?://[^"]+)"[^>]*>(.*?)</a>', raw, re.I | re.S):
            prefix = raw[max(0, match.start() - 1400):match.start()].lower()
            if "data-type=\"web\"" not in prefix and "snippet" not in prefix:
                continue
            tail = raw[match.end():match.end() + 1800]
            snippet = re.search(r'(?:generic-snippet|snippet-description|result-description)[^>]*>(.*?)</(?:div|p)>', tail, re.I | re.S)
            values.append(WebSource(url=match.group(1), title=_clean(match.group(2), 300), text=_clean(snippet.group(1) if snippet else ""), freshness="search_index", evidence_level="indexed_summary"))
        return values

    @staticmethod
    def _bing_links(raw: str) -> list[WebSource]:
        values = []
        starts = list(re.finditer(r'<li\b[^>]*\bclass="[^"]*\bb_algo\b[^"]*"[^>]*>', raw, re.I))
        for index, match in enumerate(starts):
            node = raw[match.end():starts[index + 1].start() if index + 1 < len(starts) else match.end() + 12000]
            link = re.search(r'<h2[^>]*>.*?<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', node, re.I | re.S)
            if not link:
                continue
            target = SearchProvider._result_url(link.group(1))
            if not target:
                continue
            snippet = re.search(r'<p[^>]*>(.*?)</p>', node, re.I | re.S)
            values.append(WebSource(url=target, title=_clean(link.group(2), 300), text=_clean(snippet.group(1) if snippet else ""), freshness="search_index", evidence_level="indexed_summary"))
        return values

    @staticmethod
    def _result_url(raw_url: str) -> str:
        """Decode Bing's documented click wrapper without accepting non-web URLs."""
        candidate = html.unescape(raw_url).strip()
        parsed = urlsplit(candidate)
        if parsed.hostname and parsed.hostname.lower() == "www.bing.com" and parsed.path.startswith("/ck/"):
            wrapped = parse_qs(parsed.query).get("u", [""])[0]
            if wrapped.startswith("a1"):
                encoded = wrapped[2:]
                try:
                    candidate = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
                except (UnicodeDecodeError, ValueError):
                    return ""
        target = urlsplit(candidate)
        return candidate if target.scheme in {"http", "https"} and bool(target.hostname) else ""

    @staticmethod
    def _baidu_links(raw: str) -> list[WebSource]:
        values = []
        for node in re.findall(r'<div\b[^>]*\bclass="[^"]*\bresult\b[^"]*"[^>]*>(.*?)</div>\s*</div>', raw, re.I | re.S):
            link = re.search(r'<h3[^>]*>.*?<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', node, re.I | re.S)
            if link:
                values.append(WebSource(url=link.group(1), title=_clean(link.group(2), 300), text=_clean(node), freshness="search_index", evidence_level="indexed_summary"))
        return values

    @staticmethod
    def _ddg_links(raw: str) -> list[WebSource]:
        values = []
        for node in re.findall(r'<(?:article|div)\b[^>]*(?:data-testid="result"|class="[^"]*\bresult\b[^"]*")[^>]*>(.*?)</(?:article|div)>', raw, re.I | re.S):
            link = re.search(r'<a\b[^>]*\bclass="[^"]*result__a[^"]*"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', node, re.I | re.S)
            if link:
                values.append(WebSource(url=link.group(1), title=_clean(link.group(2), 300), text=_clean(node), freshness="search_index", evidence_level="indexed_summary"))
        return values

    @staticmethod
    def _rss_links(raw: str) -> list[WebSource]:
        values = []
        for item in re.findall(r"<item>(.*?)</item>", raw, re.I | re.S):
            link = re.search(r"<link>(.*?)</link>", item, re.I | re.S); title = re.search(r"<title>(.*?)</title>", item, re.I | re.S); summary = re.search(r"<description>(.*?)</description>", item, re.I | re.S)
            if link and link.group(1).startswith("http"):
                values.append(WebSource(url=link.group(1).strip(), title=_clean(title.group(1) if title else "", 300), text=_clean(summary.group(1) if summary else ""), freshness="search_index", evidence_level="indexed_summary"))
        return values

    @classmethod
    def _brave_in_cooldown(cls) -> bool:
        with cls._brave_lock:
            return time.monotonic() < cls._brave_cooldown_until

    @classmethod
    def _trip_brave_cooldown(cls) -> None:
        with cls._brave_lock:
            cls._brave_cooldown_until = time.monotonic() + cls._brave_cooldown_seconds


class GitHubProvider:
    _KNOWN_REPOSITORIES = {"langgraph": "langchain-ai/langgraph"}

    def __init__(self, http):
        self.http = http

    def search(self, query):
        repository = self._repository_hint(query.query) or self._best_repository(query)
        return self._release_or_fallback(repository)

    def _repository_hint(self, text: str) -> str:
        match = re.search(r"github\.com/([\w.-]+/[\w.-]+)", text, re.I)
        if match:
            return match.group(1)
        normalized = " ".join(core_entities(text))
        return next((repository for entity, repository in self._KNOWN_REPOSITORIES.items() if entity in normalized), "")

    def _best_repository(self, query: WebQuery) -> str:
        terms = core_entities(query.query) or [query.query]
        response = self.http.get(f"https://api.github.com/search/repositories?q={quote_plus(' '.join(terms))}&per_page=10", headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        items = [item for item in response.json().get("items", []) if isinstance(item, dict) and item.get("full_name")]
        if not items:
            raise LookupError("GitHub repository search returned no match")
        def score(item):
            name = str(item.get("name") or "").casefold(); full = str(item.get("full_name") or "").casefold(); owner = str((item.get("owner") or {}).get("login") or "").casefold(); description = str(item.get("description") or "").casefold()
            return sum((40 if term == name else 18 if term in full else 8 if term in description or term in owner else 0) for term in terms) + (3 if not item.get("archived") else -20) + min(8, int(item.get("stargazers_count") or 0) // 10000)
        return str(max(items, key=score)["full_name"])

    def _release_or_fallback(self, repository: str) -> list[WebSource]:
        headers = {"Accept": "application/vnd.github+json"}
        release = self.http.get(f"https://api.github.com/repos/{repository}/releases/latest", headers=headers)
        if release.status_code != 404:
            release.raise_for_status()
            return [self._release_source(repository, release.json())]
        tags = self.http.get(f"https://api.github.com/repos/{repository}/tags?per_page=1", headers=headers)
        if tags.status_code != 404:
            tags.raise_for_status()
            rows = tags.json()
            if rows:
                tag = rows[0]
                return [WebSource(source_type="tag", platform="github", url=f"https://github.com/{repository}/releases/tag/{tag.get('name')}", title=f"{repository} {tag.get('name') or 'tag'}", text="GitHub latest tag fallback", freshness="official_api", evidence_level="github_rest_tag", official_account=True, authority="official_github_org")]
        commits = self.http.get(f"https://api.github.com/repos/{repository}/commits?per_page=1", headers=headers)
        commits.raise_for_status()
        rows = commits.json()
        if not rows:
            raise LookupError("GitHub repository has no release, tag, or commit")
        commit = rows[0]
        date = str((((commit.get("commit") or {}).get("author") or {}).get("date") or ""))
        return [WebSource(source_type="commit", platform="github", url=str(commit.get("html_url") or f"https://github.com/{repository}/commits"), title=f"{repository} latest commit", text=str((commit.get("commit") or {}).get("message") or ""), freshness="official_api", evidence_level="github_rest_commit", official_account=True, authority="official_github_org", published_at=date)]

    @staticmethod
    def _release_source(repository: str, item: dict) -> WebSource:
        return WebSource(source_type="release", platform="github", author=str((item.get("author") or {}).get("login") or ""), url=str(item.get("html_url") or f"https://github.com/{repository}/releases"), title=f"{repository} {item.get('tag_name') or item.get('name') or 'release'}", text=str(item.get("body") or item.get("name") or ""), published_at=str(item.get("published_at") or item.get("created_at") or ""), freshness="official_api", evidence_level="github_rest_release", official_account=True, authority="official_github_org")


class SocialProvider:
    def __init__(self, http, search): self.http, self.search_provider = http, search
    def search(self, query, platform):
        if platform=="bilibili":
            response=self.http.get(f"https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={quote_plus(query.query)}"); response.raise_for_status(); return [WebSource(source_type="video",platform="bilibili",author=str(item.get("author")or""),url=str(item.get("arcurl")or""),title=re.sub(r"<[^>]+>","",str(item.get("title")or"")),text=re.sub(r"<[^>]+>","",str(item.get("description")or"")),freshness="public_api",evidence_level="bilibili_public_api") for item in (response.json().get("data")or{}).get("result",[]) if str(item.get("arcurl")or"").startswith("http")],"bilibili_public_api"
        if platform=="bluesky": return self._bluesky(query),"bluesky_public"
        if platform=="mastodon": return self._mastodon(query),"mastodon_public"
        if platform=="reddit": return self._reddit(query),"reddit_public"
        if platform=="x": return self._x(query),"x_oembed_or_index"
        domain = "xiaohongshu.com" if platform == "xiaohongshu" else f"{platform}.com"
        values, _, providers = self.search_provider.search(WebQuery(query=f"site:{domain} {query.query}"))
        result = [item.model_copy(update={"platform": platform if self._matches_platform_url(platform, item.url) else "web", "evidence_level": "indexed_summary"}) for item in values]
        return result, providers[0] if providers else "search_index"
    @staticmethod
    def _matches_platform_url(platform: str, url: str) -> bool:
        if platform == "xiaohongshu": return bool(re.search(r"(?:^|\.)xiaohongshu\.com/(?:explore|discovery/item)/[^/?#]+", url, re.I))
        return bool(re.search(rf"(?:^|\.){re.escape(platform)}\.com/", url, re.I))
    def _bluesky(self,q):
        r=self.http.get(f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={quote_plus(q.query)}&limit=5");r.raise_for_status();return []
    def _mastodon(self,q):
        r=self.http.get(f"https://mastodon.social/api/v2/search?q={quote_plus(q.query)}&type=statuses");r.raise_for_status();return []
    def _reddit(self,q):
        r=self.http.get(f"https://www.reddit.com/search.json?q={quote_plus(q.query)}&limit=5");r.raise_for_status();return []
    def _x(self,q):
        values,_,_=self.search_provider.search(WebQuery(query=f"site:x.com {q.query}")); result=[]
        for item in values:
            if not re.search(r"(?:x\.com|twitter\.com)/[^/]+/status/\d+",item.url,re.I): continue
            try: r=self.http.get(f"https://publish.twitter.com/oembed?url={quote_plus(item.url)}");r.raise_for_status();p=r.json();result.append(WebSource(source_type="oembed",platform="x",url=item.url,author=str(p.get("author_name")or""),text=re.sub(r"<[^>]+>","",str(p.get("html")or"")),freshness="public_api",evidence_level="x_oembed"))
            except Exception: result.append(item.model_copy(update={"platform":"x","evidence_level":"indexed_summary","freshness":"search_index"}))
        return result
