"""Opt-in dependency storage checks on an isolated managed PostgreSQL test DB."""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.db.config import DatabaseSettings
from app.db.models import DependencyRelationshipRow, RepositoryRow
from app.db.session import create_database_engine, create_session_factory
from app.modules.dependency_intelligence.persistence import DependencyPersistence
from app.modules.dependency_intelligence.schemas import DependencyRelationship
from app.modules.repository_ingestion.registry import RepositoryRecord


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_REMOTE_DB_TESTS") != "1",
    reason="Requires explicit opt-in and isolated migrated managed PostgreSQL test database.",
)
def test_remote_edges_idempotency_scope_fk_and_restart():
    test_url = os.environ.get("ARCHMIND_TEST_DATABASE_URL")
    assert test_url and test_url != os.environ.get("DATABASE_URL")
    settings = DatabaseSettings(database_url=test_url)
    database_name = settings.sqlalchemy_url().database or ""
    assert database_name.startswith("test_") or database_name.endswith("_test")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    first_id, second_id = uuid4(), uuid4()

    def record(identifier):
        return RepositoryRecord(
            repository_id=identifier, name=f"test-{identifier}",
            source_alias=f"test-{identifier}", root_path=Path("/unused"),
            status="REGISTERED", registered_at=datetime.now(timezone.utc),
        )

    def edge(identifier, name):
        return DependencyRelationship(
            relationship_id=uuid4().hex.ljust(64, "0"),
            repository_id=identifier, source_symbol=f"demo.{name}",
            target_symbol="demo.Target", relationship_type="CALLS",
            file_path="src/Test.java", start_line=2, end_line=2,
            resolution_status="RESOLVED",
        )

    first, second = edge(first_id, "First"), edge(second_id, "Second")
    try:
        with factory() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20261031_0005"
        store = DependencyPersistence(factory)
        assert store.replace_snapshot(record(first_id), [first]) == 1
        assert store.replace_snapshot(record(first_id), [first]) == 1
        assert store.replace_snapshot(record(second_id), [second]) == 1
        total, values = store.list_relationships(
            first_id, symbol=None, relationship_type=None, direction="both", limit=10,
        )
        assert total == 1 and values[0].relationship_id == first.relationship_id
        restarted_engine = create_database_engine(settings)
        try:
            restarted = DependencyPersistence(create_session_factory(restarted_engine))
            assert restarted.list_relationships(
                first_id, symbol=None, relationship_type=None, direction="both", limit=10,
            )[0] == 1
        finally:
            restarted_engine.dispose()
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.execute(DependencyRelationshipRow.__table__.insert().values(
                    relationship_id=uuid4().hex.ljust(64, "0"),
                    repository_id=uuid4(), source_symbol="orphan",
                    target_symbol="target", relationship_type="CALLS",
                    file_path="Orphan.java", start_line=1, end_line=1,
                    resolution_status="UNRESOLVED",
                ))
        assert store.replace_snapshot(record(first_id), []) == 0
        assert store.list_relationships(
            first_id, symbol=None, relationship_type=None, direction="both", limit=10,
        )[0] == 0
        assert store.list_relationships(
            second_id, symbol=None, relationship_type=None, direction="both", limit=10,
        )[0] == 1
    finally:
        with factory.begin() as session:
            session.execute(delete(DependencyRelationshipRow).where(
                DependencyRelationshipRow.repository_id.in_([first_id, second_id])
            ))
            session.execute(delete(RepositoryRow).where(RepositoryRow.id.in_([first_id, second_id])))
        engine.dispose()
