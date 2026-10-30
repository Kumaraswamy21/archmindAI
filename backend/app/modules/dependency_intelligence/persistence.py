"""Transactional repository-scoped PostgreSQL edge storage."""

from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DependencyRelationshipRow, RepositoryRow
from app.modules.dependency_intelligence.schemas import (
    DependencyRelationship, Direction, RelationshipType,
)
from app.modules.repository_ingestion.registry import RepositoryRecord


class DependencyPersistence:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def replace_snapshot(
        self, repository: RepositoryRecord, relationships: list[DependencyRelationship],
    ) -> int:
        ids = {item.relationship_id for item in relationships}
        with self._session_factory.begin() as session:
            session.execute(
                insert(RepositoryRow).values(
                    id=repository.repository_id, name=repository.name,
                    source_alias=repository.source_alias, status=repository.status,
                    created_at=repository.registered_at,
                ).on_conflict_do_nothing(index_elements=[RepositoryRow.id])
            )
            existing = session.scalars(
                select(DependencyRelationshipRow.relationship_id).where(
                    DependencyRelationshipRow.repository_id == repository.repository_id
                )
            ).all()
            stale = [item for item in existing if item not in ids]
            for start in range(0, len(stale), 500):
                session.execute(
                    delete(DependencyRelationshipRow).where(
                        DependencyRelationshipRow.repository_id == repository.repository_id,
                        DependencyRelationshipRow.relationship_id.in_(stale[start:start + 500]),
                    )
                )
            for relationship in relationships:
                values = relationship.model_dump()
                session.execute(
                    insert(DependencyRelationshipRow).values(**values).on_conflict_do_update(
                        index_elements=[DependencyRelationshipRow.relationship_id],
                        set_={"resolution_status": relationship.resolution_status},
                    )
                )
        return len(relationships)

    def list_relationships(
        self, repository_id: UUID, *, symbol: str | None,
        relationship_type: RelationshipType | None, direction: Direction,
        limit: int,
    ) -> tuple[int, list[DependencyRelationship]] | None:
        with self._session_factory() as session:
            if session.get(RepositoryRow, repository_id) is None:
                return None
            filters = [DependencyRelationshipRow.repository_id == repository_id]
            if relationship_type is not None:
                filters.append(DependencyRelationshipRow.relationship_type == relationship_type)
            if symbol is not None:
                outgoing = DependencyRelationshipRow.source_symbol == symbol
                incoming = DependencyRelationshipRow.target_symbol == symbol
                filters.append(
                    outgoing if direction == "outgoing" else
                    incoming if direction == "incoming" else
                    or_(outgoing, incoming)
                )
            total = session.scalar(
                select(func.count()).select_from(DependencyRelationshipRow).where(*filters)
            )
            rows = session.scalars(
                select(DependencyRelationshipRow).where(*filters)
                .order_by(
                    DependencyRelationshipRow.file_path,
                    DependencyRelationshipRow.start_line,
                    DependencyRelationshipRow.end_line,
                    DependencyRelationshipRow.relationship_type,
                    DependencyRelationshipRow.source_symbol,
                    DependencyRelationshipRow.target_symbol,
                    DependencyRelationshipRow.relationship_id,
                )
                .limit(limit)
            ).all()
            return int(total or 0), [DependencyRelationship.model_validate({
                "relationship_id": row.relationship_id,
                "repository_id": row.repository_id,
                "source_symbol": row.source_symbol,
                "target_symbol": row.target_symbol,
                "relationship_type": row.relationship_type,
                "file_path": row.file_path,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "resolution_status": row.resolution_status,
            }) for row in rows]
