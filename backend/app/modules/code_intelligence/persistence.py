"""Transactional PostgreSQL storage for generated Java chunks."""

import hashlib
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CodeChunkRow, IndexJobRow, RepositoryRow
from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.repository_ingestion.registry import RepositoryRecord


class ChunkPersistence:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def replace_file_chunks(
        self, repository: RepositoryRecord, file_path: str, chunks: list[JavaChunk]
    ) -> int:
        """Upsert the repository and replace one file's stale chunks atomically."""
        ids = [chunk.chunk_id for chunk in chunks]
        with self._session_factory.begin() as session:
            project = session.get(RepositoryRow, repository.repository_id, with_for_update=True)
            if isinstance(project, RepositoryRow):
                active_job = session.scalar(select(IndexJobRow.job_id).where(
                    IndexJobRow.repository_id == repository.repository_id,
                    IndexJobRow.status.in_(["QUEUED", "RUNNING"]),
                ).limit(1))
                if project.active_snapshot_id is not None or active_job is not None:
                    raise ApplicationError(409, "MANAGED_INDEX_REQUIRED",
                                           "Use the managed repository indexing job.")
            session.execute(
                insert(RepositoryRow).values(
                    id=repository.repository_id,
                    name=repository.name,
                    source_alias=repository.source_alias,
                    status=repository.status,
                    created_at=repository.registered_at,
                ).on_conflict_do_nothing(index_elements=[RepositoryRow.id])
            )
            stale = delete(CodeChunkRow).where(
                CodeChunkRow.repository_id == repository.repository_id,
                CodeChunkRow.file_path == file_path,
            )
            if ids:
                stale = stale.where(CodeChunkRow.chunk_id.not_in(ids))
            session.execute(stale)
            for chunk in chunks:
                values = chunk.model_dump()
                values["content_hash"] = hashlib.sha256(
                    chunk.content.encode("utf-8")
                ).hexdigest()
                session.execute(
                    insert(CodeChunkRow).values(**values).on_conflict_do_nothing(
                        index_elements=[CodeChunkRow.chunk_id]
                    )
                )
        return len(chunks)

    def list_chunks(
        self, repository_id: UUID, limit: int, offset: int
    ) -> tuple[int, list[JavaChunk]] | None:
        with self._session_factory() as session:
            if session.get(RepositoryRow, repository_id) is None:
                return None
            total = session.scalar(
                select(func.count()).select_from(CodeChunkRow).where(
                    CodeChunkRow.repository_id == repository_id
                )
            )
            rows = session.scalars(
                select(CodeChunkRow)
                .where(CodeChunkRow.repository_id == repository_id)
                .order_by(
                    CodeChunkRow.file_path,
                    CodeChunkRow.start_byte,
                    CodeChunkRow.chunk_id,
                )
                .limit(limit)
                .offset(offset)
            ).all()
            return int(total or 0), [self._to_chunk(row) for row in rows]

    @staticmethod
    def _to_chunk(row: CodeChunkRow) -> JavaChunk:
        return JavaChunk(
            chunk_id=row.chunk_id,
            repository_id=row.repository_id,
            file_path=row.file_path,
            language=row.language,
            chunk_type=row.chunk_type,
            parent_chunk_id=row.parent_chunk_id,
            sequence=row.sequence,
            split_strategy=row.split_strategy,
            start_byte=row.start_byte,
            end_byte=row.end_byte,
            symbol_name=row.symbol_name,
            qualified_name=row.qualified_name,
            parent_symbol=row.parent_symbol,
            package_name=row.package_name,
            imports=row.imports,
            annotations=row.annotations,
            start_line=row.start_line,
            end_line=row.end_line,
            content=row.content,
            contextual_header=row.contextual_header,
        )
