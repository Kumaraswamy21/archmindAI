"""Pure reciprocal-rank fusion of semantic and keyword candidates."""

from collections.abc import Sequence

from app.modules.retrieval.hybrid_persistence import ChunkIdentity
from app.modules.retrieval.hybrid_schemas import HybridSearchResult


def fuse_rrf(
    semantic_ids: Sequence[str],
    keyword_ids: Sequence[str],
    identities: dict[str, ChunkIdentity],
    *,
    rrf_constant: int,
    top_k: int,
) -> list[HybridSearchResult]:
    if rrf_constant < 1 or top_k < 1:
        raise ValueError("RRF constant and top_k must be positive.")
    ranks: dict[str, dict[str, int]] = {}
    for source, ids in (("semantic", semantic_ids), ("keyword", keyword_ids)):
        for rank, chunk_id in enumerate(ids, start=1):
            ranks.setdefault(chunk_id, {}).setdefault(source, rank)

    results = []
    for chunk_id, source_ranks in ranks.items():
        identity = identities[chunk_id]
        score = sum(
            1.0 / (rrf_constant + rank) for rank in source_ranks.values()
        )
        results.append(HybridSearchResult(
            chunk_id=chunk_id,
            file_path=identity.file_path,
            qualified_name=identity.qualified_name,
            rrf_score=score,
            retrieval_sources=[
                source for source in ("semantic", "keyword") if source in source_ranks
            ],
            source_ranks={
                source: source_ranks[source]
                for source in ("semantic", "keyword") if source in source_ranks
            },
        ))
    results.sort(key=lambda item: (
        -item.rrf_score, item.file_path, item.qualified_name, item.chunk_id,
    ))
    return results[:top_k]
