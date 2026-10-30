"""Small reranker contract and versioned metadata heuristic."""

from dataclasses import dataclass
import re
from typing import Protocol

from app.modules.retrieval.rerank_schemas import RerankResult


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: str
    file_path: str
    qualified_name: str
    symbol_name: str
    contextual_header: str
    original_rank: int


class Reranker(Protocol):
    version: str

    def rerank(self, query: str, candidates: list[RerankCandidate]) -> list[RerankResult]: ...


def _terms(value: str) -> set[str]:
    """Split punctuation, snake case and camel case into lowercase terms."""
    parts = re.findall(r"[A-Za-z]+|[0-9]+", value.replace("_", " "))
    words: set[str] = set()
    for part in parts:
        words.update(
            word.lower()
            for word in re.findall(
                r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|[0-9]+",
                part,
            )
        )
    return words


def _whole_identifiers(value: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*", value))


class MetadataHeuristicV1:
    version = "metadata_heuristic_v1"

    def rerank(self, query: str, candidates: list[RerankCandidate]) -> list[RerankResult]:
        query_identifiers = _whole_identifiers(query)
        query_terms = _terms(query)
        scored: list[tuple[float, RerankCandidate]] = []
        for candidate in candidates:
            if candidate.original_rank < 1:
                raise ValueError("Original ranks must be one-based.")
            exact_symbol = candidate.symbol_name in query_identifiers
            exact_qualified = candidate.qualified_name in query_identifiers
            structural_terms = _terms(
                f"{candidate.symbol_name} {candidate.qualified_name} {candidate.file_path}"
            )
            header_terms = _terms(candidate.contextual_header)
            denominator = len(query_terms) or 1
            score = (
                (10.0 if exact_symbol else 0.0)
                + (5.0 if exact_qualified else 0.0)
                + 2.0 * len(query_terms & structural_terms) / denominator
                + len(query_terms & header_terms) / denominator
                + 1.0 / candidate.original_rank
            )
            scored.append((score, candidate))
        scored.sort(key=lambda item: (
            -item[0], item[1].original_rank, item[1].file_path, item[1].chunk_id,
        ))
        return [
            RerankResult(
                chunk_id=candidate.chunk_id,
                file_path=candidate.file_path,
                original_rank=candidate.original_rank,
                rerankerank=new_rank,
                rerank_score=score,
                reranker_version=self.version,
            )
            for new_rank, (score, candidate) in enumerate(scored, start=1)
        ]
