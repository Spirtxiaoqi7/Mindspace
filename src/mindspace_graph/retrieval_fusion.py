"""Deterministic hybrid retrieval primitives.

The score contract is intentionally stable:

1. BM25+ and vector search produce independent rankings.
2. RRF fuses ranks without pretending incomparable raw scores are calibrated.
3. Bounded metadata boosts never exceed ``max_total_boost``.
4. An optional local cross-encoder may rerank only a small final candidate set.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mindspace_graph.models import RetrievedChunk


def tokenize(text: str) -> list[str]:
    lowered = text.casefold()
    terms = re.findall(r"[a-z0-9_]+", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return [term for term in terms if term]


class BM25Plus:
    """Small corpus-local BM25+ implementation with deterministic tie ordering."""

    def __init__(
        self, documents: list[str], *, k1: float = 1.5, b: float = 0.75, delta: float = 1.0
    ):
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.delta = delta
        self.tokens = [tokenize(document) for document in documents]
        self.lengths = [len(items) for items in self.tokens]
        self.avg_length = sum(self.lengths) / max(1, len(self.lengths))
        self.term_frequencies = [Counter(items) for items in self.tokens]
        self.document_frequency: Counter[str] = Counter()
        for items in self.tokens:
            self.document_frequency.update(set(items))

    def scores(self, query: str) -> list[float]:
        query_terms = list(dict.fromkeys(tokenize(query)))
        total = len(self.documents)
        if not query_terms or total == 0:
            return [0.0] * total
        output: list[float] = []
        for length, frequencies in zip(self.lengths, self.term_frequencies, strict=True):
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                document_frequency = self.document_frequency[term]
                inverse_frequency = math.log(
                    1.0 + (total - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                normalization = self.k1 * (
                    1.0 - self.b + self.b * length / max(self.avg_length, 1.0)
                )
                score += inverse_frequency * (
                    frequency * (self.k1 + 1.0) / (frequency + normalization) + self.delta
                )
            output.append(score)
        return output

    @staticmethod
    def relevance(raw_score: float) -> float:
        """Map positive BM25+ evidence monotonically into a stable [0, 1) range."""

        return 0.0 if raw_score <= 0 else 1.0 - math.exp(-raw_score)


def reciprocal_rank_fusion(rankings: Iterable[list[str]], *, rrf_k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = {}
    ranking_count = 0
    for ranking in rankings:
        if not ranking:
            continue
        ranking_count += 1
        for rank, identifier in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + 1.0 / (rrf_k + rank)
    if ranking_count == 0:
        return {}
    maximum = ranking_count / (rrf_k + 1)
    return {identifier: min(1.0, score / maximum) for identifier, score in fused.items()}


def bounded_boost(
    parts: dict[str, float], *, max_total: float = 0.25
) -> tuple[float, dict[str, float]]:
    accepted: dict[str, float] = {}
    remaining = max(0.0, max_total)
    for name in sorted(parts):
        value = max(0.0, float(parts[name]))
        applied = min(value, remaining)
        if applied:
            accepted[name] = applied
            remaining -= applied
        if remaining <= 0:
            break
    return sum(accepted.values()), accepted


@dataclass(slots=True)
class CrossEncoderReranker:
    """Optional offline reranker; it never downloads a model at request time."""

    model_path: str
    _model: Any | None = None
    _error: str = ""

    def _load(self) -> Any | None:
        if self._model is not None or self._error:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_path, local_files_only=True)
        except Exception as exc:  # noqa: BLE001 - hybrid result remains valid
            self._error = str(exc)
        return self._model

    def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_n: int
    ) -> list[RetrievedChunk]:
        model = self._load()
        candidates = chunks[: max(1, top_n)]
        if model is None or not candidates:
            return chunks
        values = model.predict([(query, item.text) for item in candidates])
        updated: list[RetrievedChunk] = []
        for item, raw in zip(candidates, values, strict=True):
            probability = 1.0 / (1.0 + math.exp(-float(raw)))
            metadata = dict(item.metadata)
            metadata["reranker_score"] = probability
            final_score = 0.65 * item.score + 0.35 * probability
            metadata["pre_rerank_score"] = item.score
            metadata["final_score"] = final_score
            updated.append(item.model_copy(update={"score": final_score, "metadata": metadata}))
        reranked = sorted(updated, key=lambda item: (-item.score, item.chunk_id))
        return [*reranked, *chunks[len(candidates) :]]

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.model_path),
            "ready": self._model is not None,
            "error": self._error,
            "model_path": self.model_path,
        }
