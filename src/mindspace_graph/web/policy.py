from __future__ import annotations

import re
from urllib.parse import urlsplit

from .authority import authority_for_url, is_high_risk_official_imposter, requested_brand_keys
from .models import WebQuery, WebResult, WebSource
from .relevance import source_matches_entities


_X_STATUS = re.compile(r"(?:^|\.)x\.com/[^/]+/status/\d+", re.I)
_XIAOHONGSHU_NOTE = re.compile(r"(?:^|\.)xiaohongshu\.com/(?:explore|discovery/item)/[^/?#]+", re.I)


def _hostname(source: WebSource) -> str:
    return (urlsplit(source.url).hostname or "").lower()


def _is_target_source(source: WebSource, platform: str) -> bool:
    host = _hostname(source)
    if platform == "github":
        return source.platform == "github" and source.evidence_level.startswith("github_rest") and (host == "github.com" or host.endswith(".github.com"))
    if platform == "x":
        return source.platform == "x" and source.evidence_level in {"x_oembed", "indexed_summary"} and bool(_X_STATUS.search(f"{host}{urlsplit(source.url).path}"))
    if platform == "xiaohongshu":
        return source.platform == "xiaohongshu" and source.evidence_level == "indexed_summary" and bool(_XIAOHONGSHU_NOTE.search(f"{host}{urlsplit(source.url).path}"))
    return source.platform == platform


def _is_platform_secondary(source: WebSource, targets: set[str]) -> bool:
    host = _hostname(source)
    path = urlsplit(source.url).path
    if "x" in targets:
        return source.platform == "x" and (host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com")) and bool(path)
    if "github" in targets:
        return source.platform == "github" and host == "github.com"
    if "xiaohongshu" in targets:
        return source.platform == "xiaohongshu" and host.endswith("xiaohongshu.com") and bool(path.strip("/"))
    return source.platform in targets


def finalize_result(query: WebQuery, sources: list[WebSource], failures: list[dict[str, str]], *, executed: bool = True) -> WebResult:
    intent = query.original_intent or query.query
    values = []
    for item in sources:
        if not item.url or not (item.title or item.text) or not source_matches_entities(item.url, item.title, item.text, intent) or is_high_risk_official_imposter(item.url, intent):
            continue
        authority = authority_for_url(item.url, intent)
        if requested_brand_keys(intent):
            item = item.model_copy(update={"official_account": bool(authority), "authority": authority or "secondary"})
        values.append(item)
    targets = set(query.platforms)
    primary = [item for item in values if any(_is_target_source(item, target) for target in targets)]
    secondary = [item for item in values if item not in primary and (not targets or _is_platform_secondary(item, targets))]
    selected = primary + secondary[:2] if targets else values
    if not selected:
        return WebResult(
            query=query.query,
            status="success" if executed else "failed",
            coverage="unavailable",
            failures=failures,
            reason_codes=["no_relevant_results"] if executed else ["providers_failed"],
        )

    matched = {target for target in targets if any(_is_target_source(item, target) for item in primary)}
    missing_targets = targets - matched
    strict_time_or_authority = query.scope == "official" or query.recency != "any"
    has_official_timestamp = any(item.official_account and item.published_at for item in selected)
    complete = bool(selected) and not failures and not missing_targets and any(item.freshness == "official_api" for item in selected)
    if strict_time_or_authority:
        complete = complete and has_official_timestamp
    indexed = bool(primary or not targets) and all(item.freshness == "search_index" for item in selected)
    missing_official_brand = query.scope == "official" and bool(requested_brand_keys(intent)) and not any(item.authority.startswith("official_") for item in selected)
    coverage = "complete" if complete else "unavailable" if (targets and not primary) or missing_official_brand else "indexed" if indexed and not missing_targets else "partial"
    freshness = "official_api" if complete else "search_index" if indexed else "mixed"
    return WebResult(query=query.query, status="success", coverage=coverage, freshness=freshness, sources=selected, failures=failures)
