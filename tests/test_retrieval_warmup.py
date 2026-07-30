from __future__ import annotations

import asyncio

from mindspace_graph.models import ChatRequest
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


def test_first_turn_defers_rag_but_persists_then_enables_it(tmp_path) -> None:
    async def exercise() -> None:
        settings = AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
            role_audit_enabled=False,
            context_compaction_enabled=False,
        )
        container = build_container(settings)
        container.knowledge.add_text("Mindspace 快速聊天预热索引。", source="test")
        character = container.characters.default()
        character_id = str(character["character_id"])
        session_id = "fast-chat-session"
        observed_message_counts: list[int] = []
        original_prewarm = container.knowledge.prewarm

        def tracked_prewarm(**kwargs):
            observed_message_counts.append(len(kwargs["messages"]))
            return original_prewarm(**kwargs)

        container.knowledge.prewarm = tracked_prewarm  # type: ignore[method-assign]
        first = await container.conversation.invoke(
            ChatRequest(
                message="先快速回复我",
                session_id=session_id,
                character_id=character_id,
                retrieval={"similarity_threshold": 0},
            )
        )
        assert first.status == "success"
        assert first.retrieval_counts == {"knowledge": 0, "chat": 0}
        assert "retrieve_knowledge_deferred" in first.trace
        assert "retrieve_chat_deferred" in first.trace
        assert len(container.sessions.load_all(session_id)) == 2

        warmups = list(container.conversation._retrieval_warmups.values())
        assert len(warmups) == 1
        await asyncio.gather(*warmups)
        assert observed_message_counts == [2]

        second = await container.conversation.invoke(
            ChatRequest(
                message="Mindspace 快速聊天预热索引",
                session_id=session_id,
                character_id=character_id,
                round=2,
                retrieval={"similarity_threshold": 0},
            )
        )
        assert second.status == "success"
        assert second.retrieval_counts["knowledge"] == 1
        assert "retrieve_knowledge" in second.trace
        assert len(container.sessions.load_all(session_id)) == 4
        await container.conversation.aclose()

    asyncio.run(exercise())


def test_client_cannot_force_cold_retrieval_ready(tmp_path) -> None:
    settings = AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
        role_audit_enabled=False,
    )
    container = build_container(settings)
    character_id = str(container.characters.default()["character_id"])

    resolved = container.conversation._server_request(
        ChatRequest(
            message="你好",
            session_id="cold-session",
            character_id=character_id,
            retrieval={"ready": True, "deferred_reason": ""},
        )
    )

    assert resolved.retrieval.ready is False
    assert resolved.retrieval.deferred_reason == "index_warmup_pending"
    container.conversation.close()
