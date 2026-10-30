"""Fetch only repository-owned metadata needed for heuristic reranking."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CodeChunkRow


@dataclass(frozen=True)
class RerankMetadata:
    chunk_id: str
    file_path: str
    qualified_name: str
    symbol_name: str
    contextual_header: str


class RerankMetadataStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_many(
        self, repository_id: UUID, chunk_ids: set[str]
    ) -> dict[str, RerankMetadata]:
        if not chunk_ids:
            return {}
        statement = select(
            CodeChunkRow.chunk_id,
            CodeChunkRow.file_path,
            CodeChunkRow.qualified_name,
            CodeChunkRow.symbol_name,
            CodeChunkRow.contextual_header,
        ).where(
            CodeChunkRow.repository_id == repository_id,
            CodeChunkRow.chunk_id.in_(sorted(chunk_ids)),
        )
        with self._session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = session.execute(statement).all()
        return {
            chunk_id: RerankMetadata(chunk_id, file_path, qualified_name, symbol_name, header)
            for chunk_id, file_path, qualified_name, symbol_name, header in rows
        }
