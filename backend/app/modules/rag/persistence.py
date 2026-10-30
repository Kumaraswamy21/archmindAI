"""Repository-scoped source evidence lookup from persisted chunks."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CodeChunkRow
from app.modules.rag.context_builder import EvidenceSource


class RagEvidenceStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_many(
        self, repository_id: UUID, chunk_ids: set[str]
    ) -> dict[str, EvidenceSource]:
        if not chunk_ids:
            return {}
        statement = select(
            CodeChunkRow.chunk_id,
            CodeChunkRow.repository_id,
            CodeChunkRow.file_path,
            CodeChunkRow.qualified_name,
            CodeChunkRow.start_line,
            CodeChunkRow.end_line,
            CodeChunkRow.start_byte,
            CodeChunkRow.end_byte,
            CodeChunkRow.content,
        ).where(
            CodeChunkRow.repository_id == repository_id,
            CodeChunkRow.chunk_id.in_(sorted(chunk_ids)),
        )
        with self._session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = session.execute(statement).all()
        return {
            row[0]: EvidenceSource(*row)
            for row in rows
        }
