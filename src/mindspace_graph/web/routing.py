from __future__ import annotations

import re
from typing import Any

from mindspace_graph.models import ChatRequest

from .models import RetrievalDecision

_EXPLICIT = re.compile(
    r"联网|网络搜索|搜索(?:一下)?|搜(?:一下)?|查(?:找|询|一下|查)?|打开(?:网页|链接|URL)?|核验", re.I
)
_FRESH = re.compile(r"刚刚|现在|当前|今天|最新|最近|实时|价格|比分|天气|汇率|版本|发布|更新", re.I)
_EXTERNAL = re.compile(r"官网|新闻|政策|法规|天气|汇率|价格|比赛|OpenAI|DeepSeek|Python|API|模型|软件", re.I)
_ROLE = re.compile(r"扮演|续写|创作|你爱我|你喜欢我|你在想什么|抱抱|亲吻|做爱", re.I)
_WEATHER_TOPIC = re.compile(r"天气|气温|降雨|下雨|空气质量|台风", re.I)
_ELLIPTICAL_SUBJECT = re.compile(r"^(?P<subject>[^，。！？!?]{1,40}?)(?:的)?呢[？?。!]?$", re.I)
_NON_ENTITY_SUBJECTS = {"你", "我", "我们", "你们", "这个", "那个", "这些", "那些", "还有", "别的", "另外"}

_PLATFORM_PATTERNS = {
    "x": (
        r"推文|Twitter|推特|(?:^|[\s在从])X(?:\s*(?:上|里|平台))?"
        r"(?=[\s，、。！？?!]|查|搜|找|看|打开|发布|发|的|\(|$)"
    ),
    "github": r"GitHub|仓库|release|issue|commit",
    "youtube": r"YouTube|油管",
    "reddit": r"Reddit",
    "bluesky": r"Bluesky|蓝天社交",
    "mastodon": r"Mastodon|长毛象",
    "bilibili": r"B站|Bilibili|哔哩哔哩",
    "xiaohongshu": r"小红书|Xiaohongshu|RedNote",
    "weibo": r"微博|Weibo",
    "douyin": r"抖音|Douyin|TikTok",
}

_PLATFORM_QUERY_LABELS = {
    "x": "X Twitter 推文",
    "github": "GitHub 仓库更新",
    "youtube": "YouTube 视频",
    "reddit": "Reddit 帖子",
    "bluesky": "Bluesky 帖子",
    "mastodon": "Mastodon 帖子",
    "bilibili": "B站视频",
    "xiaohongshu": "小红书帖子",
    "weibo": "微博动态",
    "douyin": "抖音视频",
}


def infer_platforms(text: str) -> list[str]:
    return [name for name, pattern in _PLATFORM_PATTERNS.items() if re.search(pattern, text, re.I)]


def _active_external_frame(history: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Recover the nearest short-lived external topic from visible dialogue."""

    visible = [
        item for item in (history or []) if not item.get("hidden") and item.get("role") in {"user", "assistant"}
    ][-8:]
    # Prefer user requests because assistant prose may mention weather or a
    # social platform figuratively while remaining ordinary roleplay.
    ordered = [item for item in reversed(visible) if item.get("role") == "user"]
    ordered.extend(item for item in reversed(visible) if item.get("role") == "assistant")
    for item in ordered:
        content = " ".join(str(item.get("content") or "").split())
        if _WEATHER_TOPIC.search(content):
            return {
                "domain": "weather",
                "recency": "today" if re.search(r"今天|今日", content) else "live",
            }
        platforms = infer_platforms(content)
        if platforms:
            return {
                "domain": "platform",
                "recency": "today" if re.search(r"今天|今日", content) else "week",
                "platforms": platforms,
                "scope": "developer" if "github" in platforms else "social",
            }
    return None


def _contextual_external_followup(text: str, history: list[dict[str, Any]] | None) -> RetrievalDecision | None:
    match = _ELLIPTICAL_SUBJECT.fullmatch(text)
    if not match:
        return None
    subject = match.group("subject").strip().removesuffix("的").strip()
    if not subject or subject in _NON_ENTITY_SUBJECTS:
        return None
    frame = _active_external_frame(history)
    if frame is None:
        return None
    if frame["domain"] == "weather":
        date = "今天" if frame["recency"] == "today" else "当前"
        return RetrievalDecision(
            mode="force",
            scope="auto",
            reason_codes=["contextual_external_entity_substitution", "weather_followup"],
            confidence=0.96,
            query=f"{subject} {date} 天气预报",
            recency=frame["recency"],
        )
    platforms = frame["platforms"]
    labels = " ".join(_PLATFORM_QUERY_LABELS[item] for item in platforms)
    return RetrievalDecision(
        mode="force",
        scope=frame["scope"],
        reason_codes=["contextual_external_entity_substitution", "platform_followup"],
        confidence=0.94,
        query=f"{subject} 最近 {labels}",
        platforms=platforms,
        recency=frame["recency"],
    )


def decide_retrieval(request: ChatRequest, history: list[dict[str, Any]] | None = None) -> RetrievalDecision:
    text = " ".join((request.message or "").split())
    if not text or _ROLE.search(text) or re.match(r"^(那)?(?:你|我|我们)(?:的)?呢[？?。!]?$", text):
        return RetrievalDecision(reason_codes=["roleplay_or_local_context"], confidence=0.96, query=text)
    contextual_followup = _contextual_external_followup(text, history)
    if contextual_followup is not None:
        return contextual_followup
    platforms = infer_platforms(text)
    scope = (
        "developer"
        if "github" in platforms
        else "official"
        if re.search(r"官方|官网", text)
        else "social"
        if platforms
        else "auto"
    )
    recency = (
        "today"
        if re.search(r"今天|今日", text)
        else "live"
        if re.search(r"刚刚|现在|当前|实时", text)
        else "week"
        if "最近" in text
        else "any"
    )
    platform_lookup = bool(platforms and re.search(r"(?:查|搜|找|打开|看|浏览)", text, re.I))
    if re.search(r"https?://", text) or _EXPLICIT.search(text) or platform_lookup:
        return RetrievalDecision(
            mode="force",
            scope=scope,
            reason_codes=["explicit_platform_lookup" if platform_lookup else "explicit_web_request"],
            confidence=0.99,
            query=text,
            platforms=platforms,
            recency=recency,
        )
    if re.search(r"(?:用|写).{0,20}(?:脚本|代码)", text):
        return RetrievalDecision(reason_codes=["creative_or_known_technical_context"], confidence=0.9, query=text)
    if (
        _FRESH.search(text)
        and (_EXTERNAL.search(text) or platforms)
        and not re.search(r"(?:用|写).{0,20}(?:脚本|代码)", text)
    ):
        return RetrievalDecision(
            mode="force",
            scope=scope,
            reason_codes=["time_sensitive_external_fact"],
            confidence=0.94,
            query=text,
            platforms=platforms,
            recency=recency,
        )
    if re.match(r"^(那(?:最新的|第二条|第三条|打开|再查|继续查)|打开第[一二三四五12345]条)", text) and any(
        (item.get("tool_execution") or {}).get("tool") == "web" or "联网" in str(item.get("content") or "")
        for item in history or []
    ):
        return RetrievalDecision(
            mode="force",
            scope=scope,
            reason_codes=["session_web_followup"],
            confidence=0.9,
            query=text,
            platforms=platforms,
            recency=recency,
        )
    if _EXTERNAL.search(text):
        return RetrievalDecision(
            mode="allow",
            scope=scope,
            reason_codes=["external_fact_may_help"],
            confidence=0.68,
            query=text,
            platforms=platforms,
            recency=recency,
        )
    return RetrievalDecision(reason_codes=["no_external_information_gap"], confidence=0.82, query=text)


def auxiliary_tool_hint(text: str) -> str:
    if re.search(r"任务|待办|提醒", text):
        return "task"
    if re.search(r"你还记得|聊天记录|之前.*说过|知识库", text):
        return "memory"
    return ""
