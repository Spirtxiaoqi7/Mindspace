"""Compatibility facade for read-only capabilities.

Web routing, providers, readers, ranking and evidence policy live in
``mindspace_graph.web``. This facade keeps the graph and older integrations on
one implementation rather than preserving a second search stack.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from mindspace_graph.models import ChatRequest
from mindspace_graph.tool_chain import ToolExecutionResult, ToolInstruction
from mindspace_graph.web import RetrievalDecision, WebOrchestrator, WebQuery
from mindspace_graph.web.routing import auxiliary_tool_hint, decide_retrieval

DEFAULT_CAPABILITY_SETTINGS: dict[str, Any] = {
    "master_enabled": True,
    "local_knowledge_enabled": True,
    "web_search_enabled": True,
    "realtime_topics_enabled": False,
    "topic_expansion_enabled": True,
    "proactive_hotspots_enabled": False,
    "show_sources_enabled": True,
    "web_timeout_seconds": 12.0,
    "max_web_results": 10,
    "max_web_pages": 6,
    "max_web_content_chars": 12000,
}


class ReadOnlyCapabilityService:
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
        self.web = WebOrchestrator(config_provider=config_provider, http_transport=http_transport)

    def close(self) -> None:
        self.web.close()

    def settings(self) -> dict[str, Any]:
        raw = self._config_provider().get("capabilities", {})
        value = dict(DEFAULT_CAPABILITY_SETTINGS)
        if isinstance(raw, dict):
            value.update({key: raw[key] for key in value if key in raw})
        return value

    def enabled(self, key: str) -> bool:
        settings = self.settings()
        return bool(settings["master_enabled"] and settings.get(key, False))

    def retrieval_decision(self, request: ChatRequest, *, history: list[dict[str, Any]] | None = None) -> RetrievalDecision:
        return decide_retrieval(request, history)

    def auxiliary_tool_hint(self, request: ChatRequest) -> str:
        return auxiliary_tool_hint(request.message)

    def execute_web(self, instruction: ToolInstruction) -> ToolExecutionResult:
        started = datetime.now(UTC)
        command = instruction.command or {}
        query = WebQuery(
            query=instruction.parameter,
            original_intent=str(command.get("original_intent") or instruction.parameter),
            scope=command.get("scope", "auto"),
            platforms=command.get("platforms", []),
            recency=command.get("recency", "any"),
            action=command.get("action", "search"),
            url=command.get("url", ""),
            find=command.get("find", ""),
        )
        result = self.web.execute(query)
        elapsed = (datetime.now(UTC) - started).total_seconds() * 1000
        execution = ToolExecutionResult(
            call_id=instruction.call_id,
            tool="web",
            level=3,
            status="success" if result.status == "success" else "failed",
            parameter_summary=instruction.parameter_summary,
            elapsed_ms=round(elapsed, 1),
            source_count=len(result.sources),
            data=result.tool_data(),
            error="" if result.status == "success" else "; ".join(item.get("error", "") for item in result.failures)[:500],
        )
        if self.audit is not None:
            self.audit.record("tool_web_executed", execution.model_dump(mode="json", exclude={"data"}))
        return execution

    # Compatibility methods delegate to the single orchestrator implementation.
    def _web_search(self, query: str, **_: Any) -> dict[str, Any]:
        return self.web.execute(WebQuery(query=query)).tool_data()

    def _web_open(self, url: str) -> dict[str, Any]:
        source = self.web.open_page(url)
        return {"document": source.product_dict(), "documents": [source.product_dict()]}

    def _fetch_document(self, _client: Any, url: str) -> dict[str, Any]:
        return self.web.open_page(url).product_dict()
