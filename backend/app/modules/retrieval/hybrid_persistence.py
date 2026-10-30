"""Resolve fused chunk identities within one repository."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CodeChunkRow


@dataclass(frozen=True)
class ChunkIdentity:
    chunk_id: str
    file_path: str
    qualified_name: str


class HybridChunkStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_identities(
        self, repository_id: UUID, chunk_ids: set[str]
    ) -> dict[str, ChunkIdentity]:
        if not chunk_ids:
            return {}
        statement = select(
            CodeChunkRow.chunk_id,
            CodeChunkRow.file_path,
            CodeChunkRow.qualified_name,
        ).where(
            CodeChunkRow.repository_id == repository_id,
            CodeChunkRow.chunk_id.in_(sorted(chunk_ids)),
        )
        with self._session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = session.execute(statement).all()
        return {
            chunk_id: ChunkIdentity(chunk_id, file_path, qualified_name)
            for chunk_id, file_path, qualified_name in rows
        }
