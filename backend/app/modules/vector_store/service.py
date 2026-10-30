"""Batch embedding orchestration over persisted AST chunks."""

import math
import time
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.vector_store.config import EmbeddingSettings
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
from app.modules.vector_store.persistence import EmbeddingMetadata, EmbeddingMetadataStore
from app.modules.vector_store.schemas import EmbeddingGenerationResponse


class EmbeddingService:
    def __init__(
        self,
        repository_service: RepositoryService,
        chunk_store: ChunkPersistence,
        metadata_store: EmbeddingMetadataStore,
        provider: EmbeddingProvider,
        settings: EmbeddingSettings,
        *,
        sleep=time.sleep,
    ) -> None:
        self._repository_service = repository_service
        self._chunk_store = chunk_store
        self._metadata_store = metadata_store
        self._provider = provider
        self._settings = settings
        self._sleep = sleep

    def generate(self, repository_id: UUID) -> EmbeddingGenerationResponse:
        generated = skipped = failed = 0
        offset = 0
        total = 0
        last_error: EmbeddingProviderError | None = None
        while True:
            try:
                page = self._chunk_store.list_chunks(repository_id, 100, offset)
            except SQLAlchemyError as exc:
                raise ApplicationError(
                    503, "DATABASE_UNAVAILABLE", "Embedding storage is unavailable."
                ) from exc
            if page is None:
                if self._repository_service.registered_record(repository_id) is None:
                    raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
                break
            total, chunks = page
            if not chunks:
                break
            page_generated, page_skipped, page_failed, page_error = self._process_page(chunks)
            generated += page_generated
            skipped += page_skipped
            failed += page_failed
            if page_error is not None:
                last_error = page_error
            offset += len(chunks)
            if offset >= total:
                break

        if failed and generated == 0 and skipped == 0 and last_error:
            status = {
                "EMBEDDING_RATE_LIMITED": 429,
                "EMBEDDING_PROVIDER_TIMEOUT": 504,
            }.get(last_error.code, 502)
            raise ApplicationError(
                status, last_error.code, "Embedding generation failed."
            )
        return EmbeddingGenerationResponse(
            repository_id=repository_id, total_chunks=total,
            generated=generated, skipped=skipped, failed=failed,
            model=self._provider.model_identifier,
        )

    def _process_page(
        self, chunks: list[JavaChunk]
    ) -> tuple[int, int, int, EmbeddingProviderError | None]:
        model = self._provider.model_identifier
        dimension = self._provider.output_dimension
        prepared = [
            (chunk, build_document_input(chunk)) for chunk in chunks
        ]
        candidates = [
            (chunk, text, embedding_input_hash(text)) for chunk, text in prepared
        ]
        try:
            existing = self._metadata_store.get_many(
                {chunk.chunk_id: input_hash for chunk, _, input_hash in candidates},
                model,
                dimension,
            )
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Embedding storage is unavailable."
            ) from exc

        pending: list[tuple[JavaChunk, str, str]] = []
        empty: list[EmbeddingMetadata] = []
        skipped = 0
        for chunk, input_text, input_hash in candidates:
            if not chunk.content.strip():
                skipped += 1
                empty.append(
                    EmbeddingMetadata(
                        chunk.chunk_id, model, dimension, EMBEDDING_INPUT_VERSION,
                        input_hash, "SKIPPED_EMPTY",
                    )
                )
                continue
            previous = existing.get(chunk.chunk_id)
            if (
                previous and previous.status == "COMPLETED"
                and previous.embedding is not None
                and previous.input_version == EMBEDDING_INPUT_VERSION
            ):
                skipped += 1
            else:
                pending.append((chunk, input_text, input_hash))
        self._save(empty)

        generated = failed = 0
        last_error: EmbeddingProviderError | None = None
        size = self._settings.embedding_batch_size
        for start in range(0, len(pending), size):
            batch = pending[start : start + size]
            try:
                vectors = self._embed_with_retry([item[1] for item in batch])
                self._validate_vectors(vectors, len(batch), dimension)
            except EmbeddingProviderError as exc:
                failed += len(batch)
                last_error = exc
                self._save([
                    EmbeddingMetadata(
                        chunk.chunk_id, model, dimension, EMBEDDING_INPUT_VERSION,
                        input_hash, "FAILED", exc.code,
                    )
                    for chunk, _, input_hash in batch
                ])
                continue
            self._save([
                EmbeddingMetadata(
                    chunk.chunk_id, model, dimension, EMBEDDING_INPUT_VERSION,
                    input_hash, "COMPLETED", embedding=vector,
                )
                for (chunk, _, input_hash), vector in zip(batch, vectors, strict=True)
            ])
            generated += len(batch)
        return generated, skipped, failed, last_error

    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        for attempt in range(self._settings.embedding_max_retries + 1):
            try:
                return self._provider.embed_documents(texts)
            except EmbeddingProviderError as exc:
                if not exc.retryable or attempt == self._settings.embedding_max_retries:
                    raise
                self._sleep(
                    min(self._settings.embedding_retry_backoff_seconds * (2 ** attempt), 5)
                )
        raise AssertionError("Retry loop did not return or raise.")

    @staticmethod
    def _validate_vectors(
        vectors: list[list[float]], expected_count: int, expected_dimension: int
    ) -> None:
        try:
            valid = len(vectors) == expected_count and all(
                len(vector) == expected_dimension
                and all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in vector
                )
                and any(vector)
                for vector in vectors
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise EmbeddingDimensionError("Embedding count or dimension mismatch.")

    def _save(self, records: list[EmbeddingMetadata]) -> None:
        try:
            self._metadata_store.upsert_many(records)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Embedding storage is unavailable."
            ) from exc
