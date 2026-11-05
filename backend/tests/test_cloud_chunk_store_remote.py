"""Opt-in checks against an explicitly isolated, migrated cloud test database."""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.db.config import DatabaseSettings
from app.db.models import CodeChunkRow, RepositoryRow
from app.db.session import create_database_engine, create_session_factory
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.registry import RepositoryRecord


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_REMOTE_DB_TESTS") != "1",
    reason="Remote integration tests require explicit opt-in and an isolated test database.",
)
def test_remote_persistence_fk_and_restart_visibility() -> None:
    test_url = os.environ.get("ARCHMIND_TEST_DATABASE_URL")
    assert test_url, "Set ARCHMIND_TEST_DATABASE_URL for the isolated remote test database."
    assert test_url != os.environ.get("DATABASE_URL"), "Test and application URLs must differ."
    settings = DatabaseSettings(database_url=test_url)
    database_name = settings.sqlalchemy_url().database or ""
    assert database_name.endswith("_test") or database_name.startswith("test_")

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    repository_id = uuid4()
    record = RepositoryRecord(
        repository_id=repository_id, name=f"test-{repository_id}",
        source_alias=f"test-{repository_id}", root_path=Path("/unused"),
        status="REGISTERED", registered_at=datetime.now(timezone.utc),
    )
    chunk = JavaChunk(
        chunk_id=uuid4().hex.ljust(64, "0"),
        repository_id=repository_id, file_path="Example.java",
        language="java", chunk_type="method", parent_chunk_id=None,
        sequence=None, split_strategy=None, start_byte=0, end_byte=12,
        symbol_name="run", qualified_name="Example.run", parent_symbol="Example",
        package_name=None, imports=[], annotations=[], start_line=1, end_line=1,
        content="void run() {}", contextual_header="Class: Example | Method: run",
    )
    try:
        with session_factory() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20261030_0004"
        store = ChunkPersistence(session_factory)
        assert store.replace_file_chunks(record, "Example.java", [chunk]) == 1
        assert store.replace_file_chunks(record, "Example.java", [chunk]) == 1
        assert store.list_chunks(repository_id, 10, 0)[0] == 1

        # A new engine and pool can still read the committed rows.
        restarted_engine = create_database_engine(settings)
        try:
            restarted = ChunkPersistence(create_session_factory(restarted_engine))
            total, chunks = restarted.list_chunks(repository_id, 10, 0)
            assert total == 1
            assert chunks[0].chunk_id == chunk.chunk_id
        finally:
            restarted_engine.dispose()

        with pytest.raises(IntegrityError):
            with session_factory.begin() as session:
                session.execute(
                    CodeChunkRow.__table__.insert().values(
                        chunk_id=uuid4().hex.ljust(64, "0"),
                        repository_id=uuid4(),
                        file_path="Orphan.java", language="java", chunk_type="method",
                        symbol_name="run", qualified_name="Orphan.run",
                        imports=[], annotations=[], start_line=1, end_line=1,
                        start_byte=0, end_byte=1, content="x",
                        contextual_header="Method: run", content_hash="0" * 64,
                    )
                )
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(CodeChunkRow).where(CodeChunkRow.repository_id == repository_id)
            )
            session.execute(delete(RepositoryRow).where(RepositoryRow.id == repository_id))
        engine.dispose()
