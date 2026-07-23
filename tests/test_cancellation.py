from __future__ import annotations

import pytest

from mindspace_graph.adapters.in_memory import (
    DeterministicLanguageModel,
    InMemoryAudit,
    InMemoryProfileRepository,
    InMemoryRetriever,
    InMemorySessionRepository,
    RegexRolePolicy,
)
from mindspace_graph.cancellation import CancellationRegistry, GenerationCancelled
from mindspace_graph.graph import build_graph
from mindspace_graph.models import ApiConfig, ChatRequest
from mindspace_graph.ports import Dependencies


class CancellingModel(DeterministicLanguageModel):
    def __init__(self, registry: CancellationRegistry, request_id: str):
        self.registry = registry
        self.request_id = request_id

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        result = super().generate(messages, config)
        self.registry.cancel(self.request_id)
        return result


def test_interruption_after_model_call_prevents_all_persistence():
    request_id = "cancel-before-write"
    registry = CancellationRegistry()
    sessions = InMemorySessionRepository()
    profiles = InMemoryProfileRepository()
    dependencies = Dependencies(
        retriever=InMemoryRetriever(),
        profiles=profiles,
        sessions=sessions,
        llm=CancellingModel(registry, request_id),
        role_policy=RegexRolePolicy(),
        audit=InMemoryAudit(),
        cancellation=registry,
    )
    graph = build_graph(dependencies)
    registry.start(request_id)

    with pytest.raises(GenerationCancelled):
        graph.invoke(
            {
                "request_id": request_id,
                "request": ChatRequest(message="停止这次回复", session_id="cancelled-session"),
            }
        )

    assert sessions.sessions == {}
    assert profiles.applied_plans == []
