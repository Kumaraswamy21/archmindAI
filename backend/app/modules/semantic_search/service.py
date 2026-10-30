"""Query embedding and repository-scoped ranking orchestration."""

import math

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.semantic_search.persistence import RankedChunk, SemanticSearchStore
from app.modules.semantic_search.schemas import SemanticSearchRequest, SemanticSearchResponse
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.embeddings.base import (
    EmbeddingDimensionError,
    EmbeddingProvider,
    EmbeddingProviderError,
)
from app.modules.vector_store.input_builder import (
    EMBEDDING_INPUT_VERSION,
    build_document_input,
    embedding_input_hash,
)


class SemanticSearchService:
    def __init__(
        self,
        repository_service: RepositoryService,
        chunk_store: ChunkPersistence,
        search_store: SemanticSearchStore,
        provider: EmbeddingProvider,
        settings: EmbeddingIndexSettings,
    ) -> None:
        self._repository_service = repository_service
        self._chunk_store = chunk_store
        self._search_store = search_store
        self._provider = provider
        self._settings = settings

    def search(self, request: SemanticSearchRequest) -> SemanticSearchResponse:
        if (
            self._provider.model_identifier != self._settings.embedding_model
            or self._provider.output_dimension != self._settings.embedding_dimension
        ):
            raise ApplicationError(409, "EMBEDDING_CONFIG_MISMATCH", "Query and document embedding configuration differ.")
        try:
            first_page = self._chunk_store.list_chunks(request.repository_id, 100, 0)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Search storage is unavailable.") from exc
        if first_page is None:
            if self._repository_service.registered_record(request.repository_id) is None:
                raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
            raise ApplicationError(409, "EMBEDDINGS_NOT_AVAILABLE", "No compatible embeddings are available for this repository.")
        total, chunks = first_page
        if total == 0:
            raise ApplicationError(409, "EMBEDDINGS_NOT_AVAILABLE", "No compatible embeddings are available for this repository.")

        try:
            vector = self._provider.embed_query(request.query)
        except EmbeddingProviderError as exc:
            status = {"EMBEDDING_RATE_LIMITED": 429, "EMBEDDING_PROVIDER_TIMEOUT": 504}.get(exc.code, 502)
            raise ApplicationError(status, exc.code, "Query embedding failed.") from exc
        if (
            len(vector) != self._settings.embedding_dimension
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
            or not any(vector)
        ):
            raise ApplicationError(502, EmbeddingDimensionError.code, "Query embedding has incompatible dimensions or values.")

        ranked: list[RankedChunk] = []
        offset = 0
        while chunks:
            hashes = {
                chunk.chunk_id: embedding_input_hash(build_document_input(chunk))
                for chunk in chunks
            }
            try:
                ranked.extend(self._search_store.search_page(
                    request.repository_id, hashes, vector,
                    self._settings.embedding_model, self._settings.embedding_dimension,
                    EMBEDDING_INPUT_VERSION, request.top_k,
                ))
            except SQLAlchemyError as exc:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Search storage is unavailable.") from exc
            offset += len(chunks)
            if offset >= total:
                break
            try:
                page = self._chunk_store.list_chunks(request.repository_id, 100, offset)
            except SQLAlchemyError as exc:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Search storage is unavailable.") from exc
            if page is None:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Repository changed during search.")
            _, chunks = page

        if not ranked:
            raise ApplicationError(409, "EMBEDDINGS_NOT_AVAILABLE", "No compatible embeddings are available for this repository.")
        ranked.sort(key=lambda item: (
            -item.result.similarity_score,
            item.result.file_path,
            item.start_byte,
            item.result.chunk_id,
        ))
        return SemanticSearchResponse(
            repository_id=request.repository_id,
            query=request.query,
            results=[item.result for item in ranked[:request.top_k]],
        )
