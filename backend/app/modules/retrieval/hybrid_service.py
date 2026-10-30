"""Fuse existing search services without issuing internal HTTP requests."""

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.retrieval.fusion import fuse_rrf
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_persistence import HybridChunkStore
from app.modules.retrieval.hybrid_schemas import HybridSearchRequest, HybridSearchResponse
from app.modules.retrieval.schemas import KeywordSearchRequest
from app.modules.retrieval.service import KeywordSearchService
from app.modules.semantic_search.schemas import SemanticSearchRequest
from app.modules.semantic_search.service import SemanticSearchService


class HybridSearchService:
    def __init__(
        self,
        semantic_service: SemanticSearchService,
        keyword_service: KeywordSearchService,
        chunk_store: HybridChunkStore,
        settings: HybridSearchSettings,
    ) -> None:
        self._semantic_service = semantic_service
        self._keyword_service = keyword_service
        self._chunk_store = chunk_store
        self._settings = settings

    def search(self, request: HybridSearchRequest) -> HybridSearchResponse:
        pool = max(request.top_k, self._settings.hybrid_candidate_pool)
        semantic = self._semantic_service.search(SemanticSearchRequest(
            repository_id=request.repository_id, query=request.query, top_k=pool,
        ))
        keyword = self._keyword_service.search(KeywordSearchRequest(
            repository_id=request.repository_id, query=request.query, top_k=pool,
        ))
        if semantic.repository_id != request.repository_id or keyword.repository_id != request.repository_id:
            raise ApplicationError(
                503, "RETRIEVAL_SCOPE_MISMATCH", "Search results changed during retrieval."
            )
        semantic_ids = [item.chunk_id for item in semantic.results]
        keyword_ids = [item.chunk_id for item in keyword.results]
        ids = set(semantic_ids) | set(keyword_ids)
        try:
            identities = self._chunk_store.get_identities(request.repository_id, ids)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Hybrid search storage is unavailable."
            ) from exc
        if set(identities) != ids:
            raise ApplicationError(
                503, "RETRIEVAL_SCOPE_MISMATCH", "Search results changed during retrieval."
            )
        return HybridSearchResponse(
            repository_id=request.repository_id,
            query=request.query,
            results=fuse_rrf(
                semantic_ids, keyword_ids, identities,
                rrf_constant=self._settings.hybrid_rrf_constant,
                top_k=request.top_k,
            ),
        )
