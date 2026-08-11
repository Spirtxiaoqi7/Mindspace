from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Coverage = Literal["complete", "partial", "indexed", "unavailable"]


class RetrievalDecision(BaseModel):
    mode: Literal["force", "allow", "suppress"] = "suppress"
    scope: Literal["auto", "official", "news", "social", "developer", "realtime"] = "auto"
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    query: str = ""
    platforms: list[str] = Field(default_factory=list)
    recency: Literal["live", "today", "week", "month", "any"] = "any"


class WebQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # Preserve the user's wording when a provider translates or compresses the
    # tool arguments.  Platform and authority policy must be based on the
    # original request, not on a model paraphrase.
    original_intent: str = ""
    scope: Literal["auto", "official", "news", "social", "developer", "realtime"] = "auto"
    platforms: list[str] = Field(default_factory=list)
    recency: Literal["live", "today", "week", "month", "any"] = "any"
    action: Literal["search", "open_page", "find_in_page"] = "search"
    url: str = ""
    find: str = ""


class WebSource(BaseModel):
    source_type: str = "web_page"
    platform: str = "web"
    author: str = ""
    handle: str = ""
    url: str
    title: str = ""
    text: str = ""
    published_at: str = ""
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    freshness: str = "search_index"
    evidence_level: str = "indexed_summary"
    official_account: bool = False
    authority: str = ""
    source: str = ""
    def product_dict(self):
        data = self.model_dump(mode="json"); data["content"] = data["text"]; return data


class WebResult(BaseModel):
    query: str
    status: Literal["success", "failed"]
    coverage: Coverage
    freshness: str = ""
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    sources: list[WebSource] = Field(default_factory=list)
    failures: list[dict[str, str]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    def tool_data(self):
        return {"query": self.query, "status": self.status, "coverage": self.coverage, "freshness": self.freshness, "retrieved_at": self.retrieved_at, "sources": [item.product_dict() for item in self.sources], "partial_failures": self.failures, "reason_codes": self.reason_codes, "count": len(self.sources)}
