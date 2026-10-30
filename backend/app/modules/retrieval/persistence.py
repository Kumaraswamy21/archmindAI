"""Parameterized, repository-scoped PostgreSQL full-text and symbol queries."""

from uuid import UUID

from sqlalchemy import case, func, literal_column, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CodeChunkRow, RepositoryRow
from app.modules.retrieval.schemas import KeywordSearchResult


class KeywordSearchStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search(
        self, repository_id: UUID, query: str, top_k: int
    ) -> list[KeywordSearchResult] | None:
        exact = or_(CodeChunkRow.symbol_name == query, CodeChunkRow.qualified_name == query)
        exact_parent = CodeChunkRow.parent_symbol == query
        tsquery = func.websearch_to_tsquery(literal_column("'simple'::regconfig"), query)
        full_text = CodeChunkRow.search_vector.op("@@")(tsquery)
        priority = case((exact, 0), (exact_parent, 1), else_=2)
        match_type = case(
            (exact, "EXACT_SYMBOL"),
            (exact_parent, "EXACT_PARENT_SYMBOL"),
            else_="FULL_TEXT",
        ).label("match_type")
        score = case(
            (exact, 1.0),
            (exact_parent, 0.9),
            else_=func.ts_rank_cd(CodeChunkRow.search_vector, tsquery),
        ).label("score")
        statement = (
            select(
                CodeChunkRow.chunk_id,
                CodeChunkRow.file_path,
                CodeChunkRow.symbol_name,
                CodeChunkRow.start_line,
                CodeChunkRow.end_line,
                match_type,
                score,
            )
            .where(
                CodeChunkRow.repository_id == repository_id,
                or_(exact, exact_parent, full_text),
            )
            .order_by(
                priority,
                score.desc(),
                CodeChunkRow.file_path,
                CodeChunkRow.start_byte,
                CodeChunkRow.chunk_id,
            )
            .limit(top_k)
        )
        with self._session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            if session.get(RepositoryRow, repository_id) is None:
                return None
            rows = session.execute(statement).all()
        return [
            KeywordSearchResult(
                chunk_id=chunk_id,
                file_path=file_path,
                symbol_name=symbol_name,
                start_line=start_line,
                end_line=end_line,
                match_type=kind,
                score=float(rank),
            )
            for chunk_id, file_path, symbol_name, start_line, end_line, kind, rank in rows
        ]
