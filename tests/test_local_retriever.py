from __future__ import annotations

import json

import numpy as np

from mindspace_graph.adapters.file_storage import JsonSessionRepository
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever


def test_parent_child_chunking_expands_retrieved_context(tmp_path):
    retriever = LocalKnowledgeRetriever(
        tmp_path / "knowledge.json",
        JsonSessionRepository(tmp_path / "sessions"),
    )
    text = "甲" * 900 + "关键事实" + "乙" * 900

    chunk_ids = retriever.add_text(
        text,
        child_size=400,
        parent_size=1000,
        overlap=100,
        source="test",
    )

    records = retriever.list_knowledge("关键事实")
    assert len(chunk_ids) > 2
    assert records
    matching = next(item for item in records if "关键事实" in item["text"])
    assert matching["parent_id"].startswith("parent-")
    assert len(matching["parent_text"]) > len(matching["text"])
    result = retriever.search_knowledge("关键事实", 1)[0]
    assert result.text == matching["parent_text"]
    assert result.metadata["child_text"] == matching["text"]
    assert retriever.stats()["parents"] == 2


class RecordingEmbeddingModel:
    def __init__(self):
        self.calls: list[list[str]] = []

    def encode(self, texts, **_kwargs):
        self.calls.append(list(texts))
        return np.asarray([[1.0, 0.0] for _text in texts])


def test_stored_vectors_are_not_recomputed_and_knowledge_file_is_cached(tmp_path):
    path = tmp_path / "knowledge.json"
    path.write_text(
        json.dumps(
            [
                {"chunk_id": "stored", "text": "已有向量", "embedding": [1.0, 0.0]},
                {"chunk_id": "missing", "text": "首次计算"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    retriever = LocalKnowledgeRetriever(path, JsonSessionRepository(tmp_path / "sessions"))
    model = RecordingEmbeddingModel()
    retriever._embedding_model = model
    retriever._embedding_load_attempted = True

    retriever.search_knowledge("查询", 5)
    retriever.search_knowledge("查询", 5)

    assert model.calls == [["查询", "首次计算"]]


def test_chat_retrieval_reuses_history_loaded_by_graph(tmp_path):
    sessions = JsonSessionRepository(tmp_path / "sessions")
    retriever = LocalKnowledgeRetriever(tmp_path / "knowledge.json", sessions)
    model = RecordingEmbeddingModel()
    retriever._embedding_model = model
    retriever._embedding_load_attempted = True
    sessions.load_session = lambda _session_id: (_ for _ in ()).throw(
        AssertionError("session must not be loaded twice")
    )

    results = retriever.search_chat(
        "查询",
        "session",
        5,
        messages=[{"message_id": "m1", "content": "复用历史", "role": "user", "round": 1}],
    )

    assert results
    assert model.calls == [["查询", "复用历史"]]
