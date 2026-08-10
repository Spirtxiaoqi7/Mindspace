from __future__ import annotations

import asyncio

from mindspace_graph.models import ChatRequest, RetrievedChunk
from mindspace_graph.nodes import NodeFactory
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


def test_first_fifteen_rounds_index_only_then_round_sixteen_enables_rag(tmp_path) -> None:
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
        for round_num in range(1, 16):
            response = await container.conversation.invoke(
                ChatRequest(
                    message=f"第{round_num}轮只建立索引",
                    session_id=session_id,
                    character_id=character_id,
                    round=round_num,
                    retrieval={"similarity_threshold": 0},
                )
            )
            assert response.status == "success"
            assert response.retrieval_counts == {"knowledge": 0, "chat": 0, "history": 0}
            assert "retrieve_chat_deferred" in response.trace
            if round_num == 1:
                warmups = list(container.conversation._retrieval_warmups.values())
                assert len(warmups) == 1
                await asyncio.gather(*warmups)
        assert observed_message_counts == [2]
        assert len(container.sessions.load_all(session_id)) == 30
        assert len(container.sessions.list_chunks(session_id)) == 15

        sixteenth = await container.conversation.invoke(
            ChatRequest(
                message="Mindspace 快速聊天预热索引",
                session_id=session_id,
                character_id=character_id,
                round=16,
                retrieval={"similarity_threshold": 0},
            )
        )
        assert sixteenth.status == "success"
        assert sixteenth.retrieval_counts["knowledge"] == 0
        assert "retrieve_knowledge" not in sixteenth.trace
        assert len(container.sessions.load_all(session_id)) == 32
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
    assert resolved.retrieval.deferred_reason == "index_build_only_first_15_rounds"
    container.conversation.close()


def test_rank_context_excludes_automatic_knowledge_and_enforces_chat_memory_quotas(tmp_path) -> None:
    settings = AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
        role_audit_enabled=False,
        context_compaction_enabled=False,
    )
    container = build_container(settings)
    chunks = [
        RetrievedChunk(
            chunk_id=f"{source}-{index}",
            text=f"{source}{index}",
            source=source,
            score=1,
        )
        for source, count in (("chat", 6), ("memory", 6))
        for index in range(count)
    ]
    result = NodeFactory(container.conversation.dependencies).rank_context(
        {
            "request": ChatRequest(message="测试", round=16),
            "chat_chunks": chunks,
        },
        lambda _event: None,
    )
    ranked = result["ranked_context"]

    assert [item.source for item in ranked] == [
        "chat",
        "chat",
        "chat",
        "memory",
        "memory",
        "memory",
    ]
    container.conversation.close()
