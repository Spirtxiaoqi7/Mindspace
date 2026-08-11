"""Authority classification for official-source retrieval claims."""

from __future__ import annotations

from urllib.parse import urlsplit


_BRANDS = {
    "deepseek": ({"deepseek", "深度求索"}, {"deepseek.com"}, {"deepseek-ai"}),
    "openai": ({"openai"}, {"openai.com"}, {"openai"}),
}


def _registrable_domain(url: str) -> str:
    labels = (urlsplit(url).hostname or "").lower().strip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else ""


def _github_org(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() != "github.com":
        return ""
    return next((part.lower() for part in parsed.path.split("/") if part), "")


def requested_brand_keys(query: str) -> set[str]:
    text = (query or "").casefold()
    return {key for key, (aliases, _domains, _orgs) in _BRANDS.items() if any(alias in text for alias in aliases)}


def authority_for_url(url: str, query: str) -> str:
    domain = _registrable_domain(url)
    org = _github_org(url)
    parsed = urlsplit(url)
    social_handle = next((part.casefold() for part in parsed.path.split("/") if part), "")
    for key in requested_brand_keys(query):
        _aliases, domains, github_orgs = _BRANDS[key]
        if domain in domains:
            return "official_domain"
        if org in github_orgs:
            return "official_github_org"
        if domain in {"x.com", "twitter.com"} and key == "openai" and social_handle == "openai":
            return "official_x_account"
    return ""


def is_high_risk_official_imposter(url: str, query: str) -> bool:
    domain = _registrable_domain(url)
    if not domain or authority_for_url(url, query):
        return False
    label = domain.split(".", 1)[0].replace("-", "")
    for key in requested_brand_keys(query):
        _aliases, domains, _github_orgs = _BRANDS[key]
        canonical = next(iter(domains)).split(".", 1)[0]
        if canonical in label or label in canonical:
            return True
    return False
