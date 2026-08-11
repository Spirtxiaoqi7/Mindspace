"""Entity extraction and source relevance gates for public retrieval."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_INTENT_WORDS = {
    "official",
    "account",
    "today",
    "post",
    "posts",
    "api",
    "update",
    "updates",
    "latest",
    "release",
    "releases",
    "recent",
    "news",
    "search",
    "find",
    "open",
    "github",
    "twitter",
    "reddit",
    "youtube",
    "bluesky",
    "mastodon",
    "repo",
    "repository",
    "官方",
    "最近",
    "一周",
    "发布",
    "更新",
    "搜索",
    "查找",
    "打开",
    "账号",
    "帖子",
    "仓库",
    "版本",
}
_KNOWN_ENTITIES = {
    "openai": ("openai",),
    "deepseek": ("deepseek", "深度求索"),
    "langgraph": ("langgraph",),
}


def core_entities(query: str) -> list[str]:
    text = (query or "").casefold()
    values = [key for key, aliases in _KNOWN_ENTITIES.items() if any(alias in text for alias in aliases)]
    for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text):
        if token not in _INTENT_WORDS and token not in values:
            values.append(token)
    return values


def source_matches_entities(url: str, title: str, text: str, query: str) -> bool:
    entities = core_entities(query)
    if not entities:
        return True
    haystack = unquote(" ".join((urlsplit(url).netloc, urlsplit(url).path, title, text))).casefold().replace("-", "")
    return any(entity.replace("-", "") in haystack for entity in entities)
