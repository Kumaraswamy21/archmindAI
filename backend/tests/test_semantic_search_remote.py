"""Opt-in exact pgvector retrieval against an isolated managed test database."""

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from app.db.config import DatabaseSettings
from app.db.models import CodeChunkRow, RepositoryRow
from app.db.session import create_database_engine, create_session_factory
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.registry import RepositoryRecord
from app.modules.semantic_search.persistence import SemanticSearchStore
from app.modules.semantic_search.schemas import SemanticSearchRequest
from app.modules.semantic_search.service import SemanticSearchService
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.input_builder import EMBEDDING_INPUT_VERSION, build_document_input, embedding_input_hash
from app.modules.vector_store.persistence import EmbeddingMetadata, EmbeddingMetadataStore


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_REMOTE_DB_TESTS") != "1",
    reason="Requires explicit opt-in and an isolated managed PostgreSQL test database.",
)
def test_exact_cosine_search_is_repository_scoped() -> None:
    test_url = os.environ.get("ARCHMIND_TEST_DATABASE_URL")
    assert test_url and test_url != os.environ.get("DATABASE_URL")
    settings = DatabaseSettings(database_url=test_url)
    name = settings.sqlalchemy_url().database or ""
    assert name.startswith("test_") or name.endswith("_test")
    engine = create_database_engine(settings, register_vectors=True)
    factory = create_session_factory(engine)
    repository_id, other_id = uuid4(), uuid4()

    def repository(identifier):
        return RepositoryRecord(
            repository_id=identifier, name=f"test-{identifier}",
            source_alias=f"test-{identifier}", root_path=Path("/unused"),
            status="REGISTERED", registered_at=datetime.now(timezone.utc),
        )

    def chunk(identifier, symbol, path):
        return JavaChunk(
            chunk_id=uuid4().hex.ljust(64, "0"), repository_id=identifier,
            file_path=path, language="java", chunk_type="method",
            start_byte=0, end_byte=12, symbol_name=symbol,
            qualified_name=f"Example.{symbol}", parent_symbol="Example",
            package_name=None, imports=[], annotations=[], start_line=2, end_line=4,
            content=f"void {symbol}() {{}}", contextual_header=f"Method: {symbol}",
        )

    payment = chunk(repository_id, "processPayment", "PaymentService.java")
    order = chunk(repository_id, "createOrder", "OrderService.java")
    secret = chunk(other_id, "privateMethod", "Secret.java")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261030_0004"
        chunks = ChunkPersistence(factory)
        vectors = EmbeddingMetadataStore(factory)
        for item in (payment, order, secret):
            chunks.replace_file_chunks(repository(item.repository_id), item.file_path, [item])
        def unit(axis):
            values = [0.0] * 128
            values[axis] = 1.0
            return values
        vectors.upsert_many([
            EmbeddingMetadata(
                item.chunk_id, "gemini-embedding-2", 128, EMBEDDING_INPUT_VERSION,
                embedding_input_hash(build_document_input(item)), "COMPLETED",
                embedding=vector,
            )
            for item, vector in ((payment, unit(0)), (order, unit(1)), (secret, unit(0)))
        ])
        provider = SimpleNamespace(
            model_identifier="gemini-embedding-2", output_dimension=128,
            embed_query=lambda _query: unit(0),
        )
        search = SemanticSearchService(
            SimpleNamespace(registered_record=lambda _id: None),
            chunks, SemanticSearchStore(factory), provider,
            EmbeddingIndexSettings(embedding_dimension=128),
        )
        result = search.search(SemanticSearchRequest(
            repository_id=repository_id,
            query="Where is payment processing implemented?", top_k=2,
        ))
        assert [row.symbol_name for row in result.results] == ["processPayment", "createOrder"]
        assert result.results[0].similarity_score == pytest.approx(1.0)
        assert result.results[1].similarity_score == pytest.approx(0.0)
        assert all(row.symbol_name != "privateMethod" for row in result.results)
    finally:
        with factory.begin() as session:
            session.execute(delete(CodeChunkRow).where(CodeChunkRow.repository_id.in_([repository_id, other_id])))
            session.execute(delete(RepositoryRow).where(RepositoryRow.id.in_([repository_id, other_id])))
        engine.dispose()
