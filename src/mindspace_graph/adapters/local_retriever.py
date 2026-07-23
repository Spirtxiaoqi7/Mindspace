"""Small persistent lexical retriever used before a vector backend is configured."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from mindspace_graph.adapters.file_storage import JsonSessionRepository, _atomic_json
from mindspace_graph.models import RetrievalSettings, RetrievedChunk
from mindspace_graph.retrieval_fusion import (
    BM25Plus,
    CrossEncoderReranker,
    bounded_boost,
    reciprocal_rank_fusion,
    tokenize,
)


def _terms(text: str) -> set[str]:
    return set(tokenize(text))


def _score(query: str, text: str) -> float:
    query_terms = _terms(query)
    text_terms = _terms(text)
    if not query_terms or not text_terms:
        return 0.0
    overlap = len(query_terms & text_terms)
    return min(1.0, overlap / max(1, len(query_terms)) + (0.1 if query in text else 0))


class LocalKnowledgeRetriever:
    def __init__(
        self,
        path: Path,
        sessions: JsonSessionRepository,
        embedding_model_path: Path | None = None,
        memory_store: Any | None = None,
        reranker_model_path: Path | None = None,
    ) -> None:
        self.path = path
        self.sessions = sessions
        self.embedding_model_path = embedding_model_path
        self.memory_store = memory_store
        self.reranker = (
            CrossEncoderReranker(str(reranker_model_path)) if reranker_model_path else None
        )
        self._embedding_model: Any | None = None
        self._embedding_error = ""
        self._embedding_load_attempted = False
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._embedding_cache_limit = 2048
        self._records_cache: list[dict[str, Any]] = []
        self._records_stamp: tuple[int, int] | None = None
        self._lock = RLock()
        if not path.exists():
            _atomic_json(path, [])

    def _load(self) -> list[dict]:
        stat = self.path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if stamp == self._records_stamp:
                return list(self._records_cache)
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            records = value if isinstance(value, list) else []
            self._records_cache = records
            self._records_stamp = stamp
            return list(records)

    def _model(self) -> Any | None:
        if self._embedding_load_attempted:
            return self._embedding_model
        with self._lock:
            if self._embedding_load_attempted:
                return self._embedding_model
            self._embedding_load_attempted = True
            if not self.embedding_model_path or not self.embedding_model_path.exists():
                self._embedding_error = "embedding model directory is missing"
                return None
            try:
                from sentence_transformers import SentenceTransformer

                try:
                    self._embedding_model = SentenceTransformer(
                        str(self.embedding_model_path),
                        backend="onnx",
                        local_files_only=True,
                    )
                except Exception:  # noqa: BLE001 - retry with the portable PyTorch backend
                    self._embedding_model = SentenceTransformer(
                        str(self.embedding_model_path), local_files_only=True
                    )
            except Exception as exc:  # noqa: BLE001 - lexical fallback remains available
                self._embedding_error = str(exc)
                self._embedding_model = None
            return self._embedding_model

    def _embed(self, text: str) -> list[float] | None:
        return self._embed_many([text])[0]

    def _embed_many(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        model = self._model()
        if model is None:
            return [None for _text in texts]
        keys = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        missing: dict[str, str] = {}
        with self._lock:
            for key, text in zip(keys, texts, strict=True):
                if key in self._embedding_cache:
                    self._embedding_cache.move_to_end(key)
                else:
                    missing.setdefault(key, text)
        if missing:
            # Knowledge and chat retrieval are parallel graph branches.  Keep
            # cache admission and model encoding under one re-entrant lock so
            # both branches cannot encode the same query at the same time.
            with self._lock:
                missing = {
                    key: text for key, text in missing.items() if key not in self._embedding_cache
                }
                encoded = (
                    model.encode(
                        list(missing.values()), normalize_embeddings=True, convert_to_numpy=True
                    )
                    if missing
                    else []
                )
                for key, vector in zip(missing, encoded, strict=True):
                    self._embedding_cache[key] = [float(value) for value in vector.tolist()]
                while len(self._embedding_cache) > self._embedding_cache_limit:
                    self._embedding_cache.popitem(last=False)
        with self._lock:
            return [self._embedding_cache.get(key) for key in keys]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))

    def _hybrid_fuse(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        vectors: list[list[float] | None],
        query_vector: list[float] | None,
        settings: RetrievalSettings,
        boost_parts: list[dict[str, float]],
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        texts = [str(item.metadata.get("retrieval_text") or item.text) for item in chunks]
        bm25_raw = BM25Plus(texts).scores(query) if settings.bm25_enabled else [0.0] * len(chunks)
        bm25_relevance = [BM25Plus.relevance(value) for value in bm25_raw]
        semantic = [
            self._cosine(query_vector, vector)
            if settings.vector_enabled and query_vector is not None and vector is not None
            else 0.0
            for vector in vectors
        ]
        lexical_order = [
            chunks[index].chunk_id
            for index in sorted(
                range(len(chunks)), key=lambda i: (-bm25_raw[i], chunks[i].chunk_id)
            )
            if bm25_raw[index] > 0
        ]
        vector_order = [
            chunks[index].chunk_id
            for index in sorted(
                range(len(chunks)), key=lambda i: (-semantic[i], chunks[i].chunk_id)
            )
            if semantic[index] > 0
        ]
        fused = reciprocal_rank_fusion([lexical_order, vector_order], rrf_k=settings.rrf_k)
        lexical_rank = {identifier: rank for rank, identifier in enumerate(lexical_order, 1)}
        vector_rank = {identifier: rank for rank, identifier in enumerate(vector_order, 1)}
        output: list[RetrievedChunk] = []
        for index, chunk in enumerate(chunks):
            evidence = max(bm25_relevance[index], semantic[index])
            if evidence <= 0:
                continue
            rrf_score = fused.get(chunk.chunk_id, 0.0)
            boost, applied = bounded_boost(boost_parts[index], max_total=settings.max_total_boost)
            final_score = min(1.0, 0.70 * rrf_score + 0.30 * evidence + boost)
            metadata = dict(chunk.metadata)
            metadata.update(
                {
                    "bm25_raw": bm25_raw[index],
                    "bm25_score": bm25_relevance[index],
                    "bm25_rank": lexical_rank.get(chunk.chunk_id),
                    "vector_score": semantic[index],
                    "vector_rank": vector_rank.get(chunk.chunk_id),
                    "rrf_score": rrf_score,
                    "boosts": applied,
                    "pre_temporal_score": final_score,
                    "final_score": final_score,
                }
            )
            output.append(chunk.model_copy(update={"score": final_score, "metadata": metadata}))
        return sorted(output, key=lambda item: (-item.score, item.chunk_id))

    def search_knowledge(
        self,
        query: str,
        k: int,
        *,
        settings: RetrievalSettings | None = None,
        user_name: str = "",
        character_name: str = "",
    ) -> list[RetrievedChunk]:
        settings = settings or RetrievalSettings()
        records = self._load()
        # A parent may contain several overlapping children with the exact same
        # query. Keep the first exact child as the stable evidence anchor.
        exact_parents: set[str] = set()
        filtered_records: list[dict[str, Any]] = []
        folded_query = query.casefold()
        for item in records:
            parent_id = str(item.get("parent_id") or item.get("chunk_id") or "")
            exact = bool(folded_query and folded_query in str(item.get("text") or "").casefold())
            if exact and parent_id in exact_parents:
                continue
            if exact:
                exact_parents.add(parent_id)
            filtered_records.append(item)
        records = filtered_records
        missing_texts = [
            str(item.get("text") or "")
            for item in records
            if not isinstance(item.get("embedding"), list)
        ]
        computed = self._embed_many([query, *missing_texts])
        query_vector = computed[0]
        missing_vectors = iter(computed[1:])
        vectors = [
            item.get("embedding")
            if isinstance(item.get("embedding"), list)
            else next(missing_vectors, None)
            for item in records
        ]
        chunks: list[RetrievedChunk] = []
        boosts: list[dict[str, float]] = []
        query_terms = _terms(query)
        for item in records:
            child_text = str(item.get("text") or "")
            parent_text = str(item.get("parent_text") or child_text)
            source = str(item.get("source") or "manual")
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(item["chunk_id"]),
                    text=parent_text,
                    source="knowledge",
                    score=0,
                    physical_time=str(item.get("created_at") or ""),
                    metadata={
                        "source": source,
                        "parent_id": item.get("parent_id", ""),
                        "child_text": child_text,
                        "retrieval_text": child_text,
                    },
                )
            )
            text_terms = _terms(f"{source} {child_text} {parent_text}")
            boosts.append(
                {
                    "character_name": settings.knowledge_character_boost
                    if character_name and character_name.casefold() in parent_text.casefold()
                    else 0.0,
                    "source_match": settings.knowledge_source_boost
                    if query_terms & _terms(source)
                    else 0.0,
                    "user_name": settings.knowledge_user_boost
                    if user_name and user_name.casefold() in parent_text.casefold()
                    else 0.0,
                    "term_density": min(0.04, len(query_terms & text_terms) * 0.01),
                }
            )
        ranked = self._hybrid_fuse(query, chunks, vectors, query_vector, settings, boosts)
        if settings.reranker_enabled and self.reranker is not None:
            ranked = self.reranker.rerank(query, ranked, top_n=settings.reranker_top_n)
        return ranked[:k]

    def search_chat(
        self,
        query: str,
        session_id: str,
        k: int,
        *,
        settings: RetrievalSettings | None = None,
        user_name: str = "",
        character_name: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> list[RetrievedChunk]:
        settings = settings or RetrievalSettings()
        source_messages = (
            messages
            if messages is not None
            else self.sessions.load_session(session_id).get("messages", [])
        )
        messages = [
            item
            for item in source_messages
            if not item.get("hidden")
        ]
        memory_records = self.memory_store.list_active() if self.memory_store is not None else []
        if not messages and not memory_records:
            return []
        message_texts = [str(item.get("content") or "") for item in messages]
        missing_memory_texts = [
            str(item.get("episode", {}).get("text") or "")
            for item in memory_records
            if not isinstance(item.get("episode", {}).get("embedding"), list)
        ]
        vectors = self._embed_many([query, *message_texts, *missing_memory_texts])
        query_vector = vectors[0]
        message_vectors = vectors[1 : 1 + len(message_texts)]
        missing_memory_vectors = iter(vectors[1 + len(message_texts) :])
        memory_vectors = [
            item.get("episode", {}).get("embedding")
            if isinstance(item.get("episode", {}).get("embedding"), list)
            else next(missing_memory_vectors, None)
            for item in memory_records
        ]
        chunks: list[RetrievedChunk] = []
        chunk_vectors: list[list[float] | None] = []
        boosts: list[dict[str, float]] = []
        for index, (item, message_vector) in enumerate(zip(messages, message_vectors, strict=True)):
            content = str(item.get("content") or "")
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(item.get("message_id") or f"{session_id}:{index}"),
                    text=content,
                    source="chat",
                    score=0,
                    session_id=session_id,
                    round_num=int(item.get("round", 1)),
                    physical_time=item.get("timestamp", ""),
                    metadata={"role": item.get("role", "unknown")},
                )
            )
            chunk_vectors.append(message_vector)
            boosts.append(
                {
                    "current_session": settings.chat_session_boost,
                    "exact_phrase": settings.chat_exact_boost
                    if query.casefold() in content.casefold()
                    else 0.0,
                }
            )
        pending_embeddings: dict[str, list[float]] = {}
        if self.memory_store is not None:
            for record, message_vector in zip(memory_records, memory_vectors, strict=True):
                episode = record.get("episode", {})
                text = str(episode.get("text") or "")
                stored_vector = episode.get("embedding")
                if isinstance(stored_vector, list):
                    message_vector = stored_vector
                elif message_vector is not None:
                    pending_embeddings[
                        str(episode.get("episode_id") or record["episode_id"])
                    ] = message_vector
                chunks.append(
                    RetrievedChunk(
                        chunk_id=f"memory:{record['memory_key']}",
                        text=text,
                        source="memory",
                        score=0,
                        session_id=str(episode.get("session_id") or "") or None,
                        round_num=int(episode.get("round", 1)),
                        physical_time=str(record.get("updated_at") or ""),
                        metadata={
                            "memory_key": record["memory_key"],
                            "memory_family": record["family_key"],
                            "json_tags": record.get("json_tags", []),
                            "eligible_misses": int(record.get("eligible_misses", 0)),
                            "last_selected_round": int(record.get("last_selected_round", 0)),
                        },
                    )
                )
                chunk_vectors.append(message_vector)
                boosts.append(
                    {
                        "current_session": settings.chat_session_boost
                        if str(episode.get("session_id") or "") == session_id
                        else 0.0,
                        "exact_phrase": settings.chat_exact_boost
                        if query.casefold() in text.casefold()
                        else 0.0,
                    }
                )
        if pending_embeddings:
            set_many = getattr(self.memory_store, "set_episode_embeddings", None)
            if callable(set_many):
                set_many(pending_embeddings)
            else:
                for episode_id, vector in pending_embeddings.items():
                    self.memory_store.set_episode_embedding(episode_id, vector)
        ordered = self._hybrid_fuse(query, chunks, chunk_vectors, query_vector, settings, boosts)
        if settings.reranker_enabled and self.reranker is not None:
            ordered = self.reranker.rerank(query, ordered, top_n=settings.reranker_top_n)
        if self.memory_store is None or k < 2:
            return ordered[:k]

        # Candidate generation also reserves space for underexposed structured memories.
        # Otherwise they could be discarded before the global fair reranker sees them.
        reserve = max(1, k // 4)
        selected = ordered[: k - reserve]
        selected_ids = {item.chunk_id for item in selected}
        protected = sorted(
            (
                item
                for item in ordered
                if item.source == "memory" and item.chunk_id not in selected_ids
            ),
            key=lambda item: (
                int(item.metadata.get("eligible_misses", 0)),
                -int(item.metadata.get("last_selected_round", 0)),
                item.score,
            ),
            reverse=True,
        )
        selected.extend(protected[:reserve])
        selected_ids = {item.chunk_id for item in selected}
        if len(selected) < k:
            selected.extend(item for item in ordered if item.chunk_id not in selected_ids)
        return selected[:k]

    def record_retrieval(
        self,
        candidates: list[RetrievedChunk],
        selected: list[RetrievedChunk],
        current_round: int,
    ) -> None:
        if self.memory_store is not None:
            self.memory_store.record_retrieval(candidates, selected, current_round)

    def add_text(
        self,
        text: str,
        *,
        source: str = "manual",
        child_size: int = 700,
        parent_size: int = 1400,
        overlap: int = 100,
    ) -> list[str]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("knowledge text must not be blank")
        child_size = max(100, min(3000, child_size))
        parent_size = max(child_size, min(10000, parent_size))
        overlap = max(0, min(child_size - 1, overlap))
        stride = max(1, child_size - overlap)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        chunks: list[tuple[str, str, str]] = []
        for paragraph in paragraphs:
            for parent_start in range(0, len(paragraph), parent_size):
                parent = paragraph[parent_start : parent_start + parent_size]
                parent_id = f"parent-{uuid4().hex[:12]}"
                if len(parent) <= child_size:
                    chunks.append((parent_id, parent, parent))
                    continue
                for child_start in range(0, len(parent), stride):
                    child = parent[child_start : child_start + child_size]
                    if child:
                        chunks.append((parent_id, parent, child))
        records = self._load()
        created = []
        now = datetime.now(UTC).isoformat()
        embeddings = self._embed_many([chunk for _parent_id, _parent, chunk in chunks])
        for (parent_id, parent, chunk), embedding in zip(chunks, embeddings, strict=True):
            chunk_id = f"kb-{uuid4().hex[:12]}"
            records.append(
                {
                    "chunk_id": chunk_id,
                    "text": chunk,
                    "parent_id": parent_id,
                    "parent_text": parent,
                    "source": source,
                    "created_at": now,
                    **({"embedding": embedding} if embedding is not None else {}),
                }
            )
            created.append(chunk_id)
        with self._lock:
            _atomic_json(self.path, records)
        return created

    def list_knowledge(self, query: str = "") -> list[dict]:
        items = self._load()
        query = query.strip().lower()
        if not query:
            return items
        return [
            item
            for item in items
            if query in str(item.get("text", "")).lower()
            or query in str(item.get("parent_text", "")).lower()
            or query in str(item.get("source", "")).lower()
        ]

    def delete_chunk(self, chunk_id: str) -> bool:
        with self._lock:
            records = self._load()
            retained = [item for item in records if item.get("chunk_id") != chunk_id]
            if len(retained) == len(records):
                return False
            _atomic_json(self.path, retained)
            return True

    def clear(self) -> int:
        with self._lock:
            count = len(self._load())
            _atomic_json(self.path, [])
            return count

    def stats(self) -> dict[str, int]:
        records = self._load()
        return {
            "chunks": len(records),
            "parents": len({str(item["parent_id"]) for item in records if item.get("parent_id")}),
            "characters": sum(len(str(item.get("text", ""))) for item in records),
            "sources": len({str(item.get("source", "")) for item in records}),
        }

    def status(self) -> dict[str, Any]:
        ready = self._model() is not None
        return {
            "backend": "sentence-transformers" if ready else "lexical",
            "ready": ready,
            "model_path": str(self.embedding_model_path or ""),
            "error": self._embedding_error,
            "reranker": self.reranker.status()
            if self.reranker is not None
            else {
                "configured": False,
                "ready": False,
                "error": "local reranker model is not installed",
            },
        }
