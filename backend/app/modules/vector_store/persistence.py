"""PostgreSQL embedding metadata and model-scoped pgvector values."""

from dataclasses import dataclass

import math

from sqlalchemy import select, func, or_, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ChunkEmbeddingRow


@dataclass(frozen=True)
class EmbeddingMetadata:
    chunk_id: str
    model_identifier: str
    output_dimension: int
    input_version: str
    input_hash: str
    status: str
    error_code: str | None = None
    embedding: list[float] | None = None


class EmbeddingMetadataStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_many(
        self, input_hashes: dict[str, str], model_identifier: str, output_dimension: int
    ) -> dict[str, EmbeddingMetadata]:
        if not input_hashes:
            return {}
        with self._session_factory() as session:
            rows = session.scalars(
                select(ChunkEmbeddingRow).where(
                    tuple_(ChunkEmbeddingRow.chunk_id, ChunkEmbeddingRow.input_hash).in_(
                        list(input_hashes.items())
                    ),
                    ChunkEmbeddingRow.model_identifier == model_identifier,
                    ChunkEmbeddingRow.output_dimension == output_dimension,
                )
            ).all()
            return {
                row.chunk_id: EmbeddingMetadata(
                    chunk_id=row.chunk_id,
                    model_identifier=row.model_identifier,
                    output_dimension=row.output_dimension,
                    input_version=row.input_version,
                    input_hash=row.input_hash,
                    status=row.status,
                    error_code=row.error_code,
                    embedding=list(row.embedding) if row.embedding is not None else None,
                )
                for row in rows
            }

    def upsert_many(self, records: list[EmbeddingMetadata]) -> None:
        if not records:
            return
        for record in records:
            if record.status == "COMPLETED" and record.embedding is None:
                raise ValueError("A completed embedding must include a vector.")
            if record.embedding is not None and (
                len(record.embedding) != record.output_dimension
                or not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in record.embedding
                )
                or not any(record.embedding)
            ):
                raise ValueError("Embedding vector dimension or values are invalid.")
        with self._session_factory.begin() as session:
            for record in records:
                statement = insert(ChunkEmbeddingRow).values(
                    **record.__dict__,
                    generated_at=func.now() if record.embedding is not None else None,
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            ChunkEmbeddingRow.chunk_id,
                            ChunkEmbeddingRow.model_identifier,
                            ChunkEmbeddingRow.output_dimension,
                            ChunkEmbeddingRow.input_hash,
                        ],
                        set_={
                            "input_version": statement.excluded.input_version,
                            "input_hash": statement.excluded.input_hash,
                            "status": statement.excluded.status,
                            "error_code": statement.excluded.error_code,
                            "embedding": statement.excluded.embedding,
                            "generated_at": statement.excluded.generated_at,
                            "updated_at": func.now(),
                        },
                        where=or_(
                            statement.excluded.status == "COMPLETED",
                            ChunkEmbeddingRow.status != "COMPLETED",
                            ChunkEmbeddingRow.embedding.is_(None),
                        ),
                    )
                )
