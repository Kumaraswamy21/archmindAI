"""Opt-in pgvector integration against an isolated managed test database."""

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.db.config import DatabaseSettings
from app.db.models import CodeChunkRow, RepositoryRow
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.registry import RepositoryRecord
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.input_builder import (
    EMBEDDING_INPUT_VERSION,
    build_document_input,
    embedding_input_hash,
)
from app.modules.vector_store.persistence import EmbeddingMetadata, EmbeddingMetadataStore
from app.modules.vector_store.status import IndexStatusService


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_REMOTE_DB_TESTS") != "1",
    reason="Requires explicit opt-in and an isolated remote PostgreSQL test database.",
)
def test_pgvector_extension_persistence_and_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    test_url = os.environ.get("ARCHMIND_TEST_DATABASE_URL")
    assert test_url, "Set ARCHMIND_TEST_DATABASE_URL to an isolated managed test database."
    assert test_url != os.environ.get("DATABASE_URL"), "Test and application URLs must differ."
    settings = DatabaseSettings(database_url=test_url)
    database_name = settings.sqlalchemy_url().database or ""
    assert database_name.startswith("test_") or database_name.endswith("_test")

    plain_engine = create_database_engine(settings)
    try:
        with plain_engine.connect() as connection:
            available = connection.scalar(text(
                "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'"
            ))
            installed = connection.scalar(text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert available is not None
            assert installed is not None
            assert revision == "20261030_0004"
    finally:
        plain_engine.dispose()

    engine = create_database_engine(settings, register_vectors=True)
    session_factory = create_session_factory(engine)
    repository_id = uuid4()
    record = RepositoryRecord(
        repository_id=repository_id, name=f"test-{repository_id}",
        source_alias=f"test-{repository_id}", root_path=Path("/unused"),
        status="REGISTERED", registered_at=datetime.now(timezone.utc),
    )
    chunk = JavaChunk(
        chunk_id=uuid4().hex.ljust(64, "0"), repository_id=repository_id,
        file_path="A.java", language="java", chunk_type="method",
        parent_chunk_id=None, sequence=None, split_strategy=None,
        start_byte=0, end_byte=13, symbol_name="run", qualified_name="A.run",
        parent_symbol="A", package_name=None, imports=[], annotations=[],
        start_line=1, end_line=1, content="void run() {}",
        contextual_header="Class: A | Method: run",
    )
    input_hash = embedding_input_hash(build_document_input(chunk))
    metadata = EmbeddingMetadata(
        chunk_id=chunk.chunk_id, model_identifier="gemini-embedding-2",
        output_dimension=128, input_version=EMBEDDING_INPUT_VERSION,
        input_hash=input_hash, status="COMPLETED", embedding=[0.1] * 128,
    )
    try:
        chunks = ChunkPersistence(session_factory)
        vectors = EmbeddingMetadataStore(session_factory)
        chunks.replace_file_chunks(record, "A.java", [chunk])
        vectors.upsert_many([metadata])
        vectors.upsert_many([metadata])
        loaded = vectors.get_many(
            {chunk.chunk_id: input_hash}, "gemini-embedding-2", 128
        )
        assert len(loaded[chunk.chunk_id].embedding) == 128
        vectors.upsert_many([
            EmbeddingMetadata(
                chunk_id=chunk.chunk_id, model_identifier="gemini-embedding-2",
                output_dimension=128, input_version=EMBEDDING_INPUT_VERSION,
                input_hash="f" * 64, status="FAILED",
                error_code="EMBEDDING_PROVIDER_TIMEOUT",
            )
        ])
        assert vectors.get_many(
            {chunk.chunk_id: input_hash}, "gemini-embedding-2", 128
        )[chunk.chunk_id].embedding is not None

        restarted_engine = create_database_engine(settings, register_vectors=True)
        try:
            restarted_factory = create_session_factory(restarted_engine)
            status = IndexStatusService(
                SimpleNamespace(registered_record=lambda _id: None),
                ChunkPersistence(restarted_factory),
                EmbeddingMetadataStore(restarted_factory),
                EmbeddingIndexSettings(
                    embedding_model="gemini-embedding-2", embedding_dimension=128
                ),
            ).get_status(repository_id)
            assert status.total_chunks == 1
            assert status.embedded_chunks == 1
            assert status.index_status == "READY"
        finally:
            restarted_engine.dispose()

        monkeypatch.setenv("DATABASE_URL", test_url)
        monkeypatch.setenv("EMBEDDING_MODEL", "gemini-embedding-2")
        monkeypatch.setenv("EMBEDDING_DIMENSION", "128")
        restarted_app = create_app()
        try:
            response = TestClient(restarted_app).get(
                f"/api/v1/repositories/{repository_id}/index/status"
            )
            assert response.status_code == 200
            assert response.json()["index_status"] == "READY"
            assert "embedding" not in response.json()
        finally:
            restarted_app.state.db_engine.dispose()
    finally:
        with session_factory.begin() as session:
            session.execute(
                delete(CodeChunkRow).where(CodeChunkRow.repository_id == repository_id)
            )
            session.execute(delete(RepositoryRow).where(RepositoryRow.id == repository_id))
        engine.dispose()
