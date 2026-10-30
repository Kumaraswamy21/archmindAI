"""Opt-in PostgreSQL keyword search against an isolated managed test database."""

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
from app.modules.retrieval.persistence import KeywordSearchStore
from app.modules.retrieval.schemas import KeywordSearchRequest
from app.modules.retrieval.service import KeywordSearchService


@pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_REMOTE_DB_TESTS") != "1",
    reason="Requires explicit opt-in and an isolated managed PostgreSQL test database.",
)
def test_keyword_postgres_exact_full_text_case_and_isolation() -> None:
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

    def chunk(identifier, symbol, qualified, parent, path, content, start):
        return JavaChunk(
            chunk_id=uuid4().hex.ljust(64, "0"), repository_id=identifier,
            file_path=path, language="java", chunk_type="method",
            start_byte=start, end_byte=start + len(content.encode()),
            symbol_name=symbol, qualified_name=qualified, parent_symbol=parent,
            package_name="example", imports=[], annotations=[],
            start_line=5 + start, end_line=7 + start,
            content=content, contextual_header=f"Class: {parent} | Method: {symbol}",
        )

    exact = chunk(repository_id, "OrderService", "example.OrderService", "OrderService",
                  "src/OrderService.java", "class OrderService {}", 0)
    method = chunk(repository_id, "processPayment", "example.OrderService.processPayment",
                   "OrderService", "src/PaymentService.java", "payment gateway charge card", 0)
    other = chunk(other_id, "OrderService", "other.OrderService", "OrderService",
                  "src/Secret.java", "payment gateway private secret", 0)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261030_0004"
        chunks = ChunkPersistence(factory)
        for item in (exact, method, other):
            chunks.replace_file_chunks(repository(item.repository_id), item.file_path, [item])
        service = KeywordSearchService(
            SimpleNamespace(registered_record=lambda _id: None),
            KeywordSearchStore(factory),
        )

        def search(query, top_k=5):
            return service.search(KeywordSearchRequest(
                repository_id=repository_id, query=query, top_k=top_k,
            )).results

        by_symbol = search("OrderService")
        assert by_symbol[0].chunk_id == exact.chunk_id
        assert by_symbol[0].match_type == "EXACT_SYMBOL"
        assert by_symbol[0].score == 1.0
        assert any(item.chunk_id == method.chunk_id and item.match_type == "EXACT_PARENT_SYMBOL" for item in by_symbol)
        assert all(item.chunk_id != other.chunk_id for item in by_symbol)
        assert search("example.OrderService")[0].chunk_id == exact.chunk_id
        assert search("example.OrderService")[0].match_type == "EXACT_SYMBOL"
        assert search("processPayment", 1)[0].chunk_id == method.chunk_id
        assert search("processPayment", 1)[0].match_type == "EXACT_SYMBOL"
        assert search("orderservice")[0].match_type == "FULL_TEXT"  # exact Java names are case-sensitive
        text_results = search("payment gateway")
        assert text_results and text_results[0].chunk_id == method.chunk_id
        assert text_results[0].match_type == "FULL_TEXT"
        assert text_results[0].score > 0
        assert search("tokenwithnomatches") == []
        assert all(item.chunk_id != other.chunk_id for item in search("OrderService' OR 1=1 --"))
    finally:
        with factory.begin() as session:
            session.execute(delete(CodeChunkRow).where(CodeChunkRow.repository_id.in_([repository_id, other_id])))
            session.execute(delete(RepositoryRow).where(RepositoryRow.id.in_([repository_id, other_id])))
        engine.dispose()
