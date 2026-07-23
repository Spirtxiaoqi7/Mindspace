"""Deterministic adapters used by tests and the zero-configuration demo."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mindspace_graph.models import (
    ApiConfig,
    ChatRequest,
    DeletionEvent,
    JsonUpdatePlan,
    JsonWriteReceipt,
    ProfileBundle,
    RetrievedChunk,
    RoleValidation,
)
from mindspace_graph.ports import Dependencies


@dataclass
class InMemoryRetriever:
    knowledge: list[RetrievedChunk] = field(default_factory=list)
    chat: list[RetrievedChunk] = field(default_factory=list)

    @staticmethod
    def _search(query: str, chunks: list[RetrievedChunk], k: int) -> list[RetrievedChunk]:
        query_terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
        ranked: list[RetrievedChunk] = []
        for chunk in chunks:
            text_terms = set(re.findall(r"[\w\u4e00-\u9fff]+", chunk.text.lower()))
            lexical = len(query_terms & text_terms) / max(1, len(query_terms))
            score = max(chunk.score, lexical)
            ranked.append(chunk.model_copy(update={"score": min(1.0, score)}))
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:k]

    def search_knowledge(self, query: str, k: int, **_kwargs: Any) -> list[RetrievedChunk]:
        return self._search(query, self.knowledge, k)

    def search_chat(
        self, query: str, session_id: str, k: int, **_kwargs: Any
    ) -> list[RetrievedChunk]:
        scoped = [
            item for item in self.chat if not item.session_id or item.session_id == session_id
        ]
        return self._search(query, scoped, k)

    def record_retrieval(
        self,
        candidates: list[RetrievedChunk],
        selected: list[RetrievedChunk],
        current_round: int,
    ) -> None:
        return None


@dataclass
class InMemoryProfileRepository:
    bundle: ProfileBundle = field(
        default_factory=lambda: ProfileBundle(
            user_profile={"identity": {"preferred_name": "用户"}},
            ai_profile={"identity": {"name": "AI助手"}},
            runtime_state={"relationship_state": {}},
            revisions={"user_profile": 0, "ai_profile": 0, "runtime_state": 0},
        )
    )
    applied_plans: list[JsonUpdatePlan] = field(default_factory=list)

    def load_bundle(self) -> ProfileBundle:
        return self.bundle.model_copy(deep=True)

    def apply_json_update(self, plan: JsonUpdatePlan, *, request: ChatRequest) -> JsonWriteReceipt:
        self.applied_plans.append(plan.model_copy(deep=True))
        for target in {patch.target for patch in plan.patches}:
            self.bundle.revisions[target] += 1
        return JsonWriteReceipt(
            turn_id=plan.turn_id,
            applied=bool(plan.patches),
            patches=[patch.model_dump(mode="json") for patch in plan.patches],
        )


@dataclass
class InMemorySessionRepository:
    sessions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pending_deletions: dict[str, list[DeletionEvent]] = field(default_factory=dict)

    def load_recent(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        visible = [item for item in self.sessions.get(session_id, []) if not item.get("hidden")]
        return deepcopy(visible[-limit:])

    def load_all(self, session_id: str) -> list[dict[str, Any]]:
        return deepcopy(self.sessions.get(session_id, []))

    def load_pending_deletions(self, session_id: str) -> list[DeletionEvent]:
        return deepcopy(self.pending_deletions.get(session_id, []))

    def resolve_deletions(self, event_ids: list[str]) -> None:
        ids = set(event_ids)
        for session_id, events in self.pending_deletions.items():
            self.pending_deletions[session_id] = [
                event for event in events if event.event_id not in ids
            ]

    def persist_turn(
        self,
        request: ChatRequest,
        reply: str,
        *,
        replace_round: bool,
        write_receipt: JsonWriteReceipt,
    ) -> dict[str, str]:
        messages = self.sessions.setdefault(request.session_id, [])
        if replace_round:
            messages[:] = [item for item in messages if item.get("round") != request.round]
        timestamp = datetime.now(UTC).isoformat()
        user_message_id = uuid4().hex
        assistant_message_id = uuid4().hex
        messages.extend(
            [
                {
                    "message_id": user_message_id,
                    "role": "user",
                    "content": request.message,
                    "round": request.round,
                    "timestamp": timestamp,
                    "hidden": request.initiative,
                    "kind": "initiative_signal" if request.initiative else "message",
                    "initiative_trigger": request.initiative_trigger,
                },
                {
                    "message_id": assistant_message_id,
                    "role": "assistant",
                    "content": reply,
                    "round": request.round,
                    "timestamp": timestamp,
                    "kind": "initiative_response" if request.initiative else "message",
                    "initiative_trigger": request.initiative_trigger,
                },
            ]
        )
        return {
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
        }


class DeterministicLanguageModel:
    """A predictable model double; it proves orchestration without network calls."""

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        prompt = "\n".join(message["content"] for message in messages)
        revisions_match = re.search(r"base_revisions=(\{.*?\})", prompt)
        revisions = json.loads(revisions_match.group(1)) if revisions_match else {}
        return self._valid_output(revisions)

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        return self.generate(messages, config)

    def stream(self, messages: list[dict[str, str]], config: ApiConfig) -> Iterator[str]:
        output = self.generate(messages, config)
        for index in range(0, len(output), 11):
            yield output[index : index + 11]

    def stream_repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> Iterator[str]:
        output = self.repair(messages, raw_output, errors, config)
        for index in range(0, len(output), 11):
            yield output[index : index + 11]

    def compact(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        prompt = "\n".join(message["content"] for message in messages)
        cutoff_match = re.search(r'"cutoff_sequence"\s*:\s*(\d+)', prompt)
        cutoff = int(cutoff_match.group(1)) if cutoff_match else 0
        return json.dumps(
            {
                "summary_version": 1,
                "cutoff_sequence": cutoff,
                "dialogue_summary": "已完成对话历史的确定性压缩。",
                "open_threads": [],
                "commitments": [],
                "relationship_events": [],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def plan_capabilities(messages: list[dict[str, str]], config: ApiConfig) -> str:
        return json.dumps(
            {"decision": "direct_answer", "reason": "none", "calls": []},
            ensure_ascii=False,
        )

    @staticmethod
    def extract_memory(
        messages: list[dict[str, str]],
        config: ApiConfig,
        *,
        timeout_seconds: float,
    ) -> str:
        prompt = "\n".join(message["content"] for message in messages)
        revisions_match = re.search(r'"base_revisions"\s*:\s*(\{.*?\})', prompt)
        revisions = json.loads(revisions_match.group(1)) if revisions_match else {}
        return json.dumps(
            {
                "turn_id": "round_current",
                "base_revisions": revisions,
                "trigger": "none",
                "patches": [],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def take_usage():
        return None

    @staticmethod
    def audit_role(messages: list[dict[str, str]], config: ApiConfig) -> str:
        return json.dumps(
            {
                "is_consistent": True,
                "severity": "none",
                "confidence": 1.0,
                "evidence": [],
                "next_turn_instruction": "",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _valid_output(revisions: dict[str, int]) -> str:
        update = {
            "turn_id": "round_current",
            "base_revisions": revisions,
            "trigger": "none",
            "patches": [],
        }
        return "\n".join(
            [
                "<response>“这是由 LangGraph 调度完成的一次确定性演示回复。”</response>",
                f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>",
            ]
        )


@dataclass
class RegexRolePolicy:
    forbidden_patterns: tuple[str, ...] = (
        r"忽略.{0,8}(角色|设定)",
        r"我不再是",
    )

    def validate(
        self,
        response: str,
        *,
        request: ChatRequest,
        history: list[dict[str, Any]],
    ) -> RoleValidation:
        for pattern in self.forbidden_patterns:
            if re.search(pattern, response, flags=re.IGNORECASE):
                return RoleValidation(
                    is_valid=False,
                    layer="regex",
                    message=f"forbidden role pattern: {pattern}",
                    suggestion="保持当前角色身份",
                    confidence=1,
                )
        return RoleValidation(is_valid=True, layer="regex", message="passed", confidence=1)


@dataclass
class InMemoryAudit:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append({"event": event, "payload": deepcopy(payload)})


def demo_dependencies() -> Dependencies:
    now = datetime.now(UTC).isoformat()
    retriever = InMemoryRetriever(
        knowledge=[
            RetrievedChunk(
                chunk_id="kb-1",
                text="Mindspace 使用知识库与会话历史两类召回结果。",
                source="knowledge",
                score=0.91,
                physical_time=now,
            )
        ],
        chat=[
            RetrievedChunk(
                chunk_id="chat-1",
                text="用户此前希望先理解节点和调度流程。",
                source="chat",
                score=0.88,
                session_id="demo",
                physical_time=now,
            )
        ],
    )
    return Dependencies(
        retriever=retriever,
        profiles=InMemoryProfileRepository(),
        sessions=InMemorySessionRepository(),
        llm=DeterministicLanguageModel(),
        role_policy=RegexRolePolicy(),
        audit=InMemoryAudit(),
    )
