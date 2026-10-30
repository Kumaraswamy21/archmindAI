"""Count current persisted vectors for one configured model and dimension."""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.input_builder import (
    EMBEDDING_INPUT_VERSION,
    build_document_input,
    embedding_input_hash,
)
from app.modules.vector_store.persistence import EmbeddingMetadataStore
from app.modules.vector_store.schemas import IndexStatusResponse


class IndexStatusService:
    def __init__(
        self,
        repository_service: RepositoryService,
        chunk_store: ChunkPersistence,
        metadata_store: EmbeddingMetadataStore,
        settings: EmbeddingIndexSettings,
    ) -> None:
        self._repository_service = repository_service
        self._chunk_store = chunk_store
        self._metadata_store = metadata_store
        self._settings = settings

    def get_status(self, repository_id: UUID) -> IndexStatusResponse:
        total = embedded = failed = offset = 0
        while True:
            try:
                page = self._chunk_store.list_chunks(repository_id, 100, offset)
            except SQLAlchemyError as exc:
                raise ApplicationError(
                    503, "DATABASE_UNAVAILABLE", "Vector storage is unavailable."
                ) from exc
            if page is None:
                if self._repository_service.registered_record(repository_id) is None:
                    raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
                break
            total, chunks = page
            if not chunks:
                break
            hashes = {
                chunk.chunk_id: embedding_input_hash(build_document_input(chunk))
                for chunk in chunks
            }
            try:
                records = self._metadata_store.get_many(
                    hashes, self._settings.embedding_model,
                    self._settings.embedding_dimension,
                )
            except SQLAlchemyError as exc:
                raise ApplicationError(
                    503, "DATABASE_UNAVAILABLE", "Vector storage is unavailable."
                ) from exc
            for chunk in chunks:
                record = records.get(chunk.chunk_id)
                if (
                    record is not None
                    and record.status == "COMPLETED"
                    and record.embedding is not None
                    and len(record.embedding) == self._settings.embedding_dimension
                    and record.input_version == EMBEDDING_INPUT_VERSION
                ):
                    embedded += 1
                elif record is not None and record.status == "FAILED":
                    failed += 1
            offset += len(chunks)
            if offset >= total:
                break

        pending = total - embedded - failed
        if total == 0:
            state = "EMPTY"
        elif embedded == total:
            state = "READY"
        elif failed == total:
            state = "FAILED"
        elif embedded or failed:
            state = "PARTIAL"
        else:
            state = "PENDING"
        return IndexStatusResponse(
            repository_id=repository_id, total_chunks=total,
            embedded_chunks=embedded, pending_chunks=pending,
            failed_chunks=failed, index_status=state,
        )
