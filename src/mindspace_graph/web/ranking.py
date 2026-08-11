from urllib.parse import urlsplit

from .authority import authority_for_url, is_high_risk_official_imposter
from .models import WebQuery, WebSource
from .relevance import source_matches_entities


def rank_sources(query: WebQuery, sources: list[WebSource], *, limit: int) -> list[WebSource]:
    terms = [item.lower() for item in query.query.split() if len(item) > 1]

    def score(source):
        text = f"{source.title} {source.text}".lower()
        return (
            sum(term in text for term in terms)
            + (
                4
                if authority_for_url(source.url, query.query)
                or source.official_account
                or source.freshness == "official_api"
                else 0
            )
            - (3 if "captcha" in text else 0)
        )

    result = []
    domains = set()
    trusted = [
        item
        for item in sources
        if source_matches_entities(item.url, item.title, item.text, query.query)
        and not is_high_risk_official_imposter(item.url, query.query)
    ]
    for source in sorted({item.url: item for item in trusted}.values(), key=score, reverse=True):
        domain = urlsplit(source.url).hostname or ""
        if domain in domains and len(result) > 1:
            continue
        domains.add(domain)
        result.append(source)
        if len(result) >= limit:
            break
    return result
