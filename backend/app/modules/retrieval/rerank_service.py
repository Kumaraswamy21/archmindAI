"""Rerank a bounded hybrid candidate list using current source metadata."""

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_schemas import HybridSearchRequest
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.rerank_persistence import RerankMetadataStore
from app.modules.retrieval.rerank_schemas import RerankRequest, RerankResponse
from app.modules.retrieval.reranker import RerankCandidate, Reranker


class RerankSearchService:
    def __init__(
        self,
        hybrid_service: HybridSearchService,
        metadata_store: RerankMetadataStore,
        reranker: Reranker,
        settings: HybridSearchSettings,
    ) -> None:
        self._hybrid_service = hybrid_service
        self._metadata_store = metadata_store
        self._reranker = reranker
        self._settings = settings

    def search(self, request: RerankRequest) -> RerankResponse:
        pool = max(request.top_k, self._settings.hybrid_candidate_pool)
        hybrid = self._hybrid_service.search(HybridSearchRequest(
            repository_id=request.repository_id, query=request.query, top_k=pool,
        ))
        if hybrid.repository_id != request.repository_id:
            raise ApplicationError(
                503, "RETRIEVAL_SCOPE_MISMATCH", "Search results changed during retrieval."
            )
        ids = {item.chunk_id for item in hybrid.results}
        try:
            metadata = self._metadata_store.get_many(request.repository_id, ids)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Reranking metadata is unavailable."
            ) from exc
        if set(metadata) != ids or any(
            metadata[item.chunk_id].file_path != item.file_path
            or metadata[item.chunk_id].qualified_name != item.qualified_name
            for item in hybrid.results
        ):
            raise ApplicationError(
                503, "RETRIEVAL_SCOPE_MISMATCH", "Search results changed during retrieval."
            )
        candidates = [
            RerankCandidate(
                chunk_id=item.chunk_id,
                file_path=item.file_path,
                qualified_name=item.qualified_name,
                symbol_name=metadata[item.chunk_id].symbol_name,
                contextual_header=metadata[item.chunk_id].contextual_header,
                original_rank=rank,
            )
            for rank, item in enumerate(hybrid.results, start=1)
        ]
        ranked = self._reranker.rerank(request.query, candidates)
        return RerankResponse(
            repository_id=request.repository_id,
            reranker=self._reranker.version,
            results=ranked[:request.top_k],
        )
