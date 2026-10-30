"""Exact pgvector cosine search restricted to current repository chunks."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ChunkEmbeddingRow, CodeChunkRow
from app.modules.semantic_search.schemas import SearchResult


@dataclass(frozen=True)
class RankedChunk:
    result: SearchResult
    start_byte: int


class SemanticSearchStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search_page(
        self,
        repository_id: UUID,
        input_hashes: dict[str, str],
        query_vector: list[float],
        model_identifier: str,
        output_dimension: int,
        input_version: str,
        top_k: int,
    ) -> list[RankedChunk]:
        if not input_hashes:
            return []
        distance = ChunkEmbeddingRow.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(CodeChunkRow, distance)
            .join(ChunkEmbeddingRow, ChunkEmbeddingRow.chunk_id == CodeChunkRow.chunk_id)
            .where(
                CodeChunkRow.repository_id == repository_id,
                tuple_(ChunkEmbeddingRow.chunk_id, ChunkEmbeddingRow.input_hash).in_(
                    list(input_hashes.items())
                ),
                ChunkEmbeddingRow.model_identifier == model_identifier,
                ChunkEmbeddingRow.output_dimension == output_dimension,
                ChunkEmbeddingRow.input_version == input_version,
                ChunkEmbeddingRow.status == "COMPLETED",
                ChunkEmbeddingRow.embedding.is_not(None),
            )
            .order_by(
                distance,
                CodeChunkRow.file_path,
                CodeChunkRow.start_byte,
                CodeChunkRow.chunk_id,
            )
            .limit(top_k)
        )
        with self._session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = session.execute(statement).all()
        return [
            RankedChunk(
                result=SearchResult(
                    chunk_id=row.chunk_id,
                    file_path=row.file_path,
                    symbol_name=row.symbol_name,
                    start_line=row.start_line,
                    end_line=row.end_line,
                    similarity_score=1.0 - float(distance_value),
                    content=row.content,
                ),
                start_byte=row.start_byte,
            )
            for row, distance_value in rows
        ]
