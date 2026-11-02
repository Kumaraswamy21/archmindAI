"""Durable project metadata in the existing managed PostgreSQL database."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import RepositoryRow
from app.errors import ApplicationError


@dataclass(frozen=True)
class ProjectRecord:
    repository_id: UUID
    name: str
    source_alias: str
    status: str
    registered_at: datetime


def _project(row: RepositoryRow) -> ProjectRecord:
    return ProjectRecord(row.id, row.name, row.source_alias, row.status, row.created_at)


class ProjectStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, name: str, source_alias: str) -> ProjectRecord:
        with self._session_factory.begin() as session:
            # Serialize registrations across API workers without changing legacy rows.
            session.execute(text("SELECT pg_advisory_xact_lock(734118)"))
            existing = session.scalar(select(RepositoryRow.id).where(
                (RepositoryRow.source_alias == source_alias) |
                (func.lower(RepositoryRow.name) == name.lower())
            ).limit(1))
            if existing is not None:
                raise ApplicationError(409, "REPOSITORY_ALREADY_REGISTERED", "Repository already registered.")
            row = RepositoryRow(id=uuid4(), name=name, source_alias=source_alias,
                                status="REGISTERED", created_at=datetime.now(timezone.utc))
            session.add(row)
            session.flush()
            return _project(row)

    def get(self, repository_id: UUID) -> ProjectRecord | None:
        with self._session_factory() as session:
            row = session.get(RepositoryRow, repository_id)
            return _project(row) if row is not None else None

    def list(self, limit: int, offset: int) -> tuple[int, list[ProjectRecord]]:
        with self._session_factory() as session:
            total = session.scalar(select(func.count()).select_from(RepositoryRow)) or 0
            rows = session.scalars(select(RepositoryRow).order_by(
                RepositoryRow.created_at, RepositoryRow.id
            ).limit(limit).offset(offset)).all()
            return int(total), [_project(row) for row in rows]
