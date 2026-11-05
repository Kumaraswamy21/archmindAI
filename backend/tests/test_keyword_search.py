"""Keyword endpoint, validation, SQL construction, and controlled failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from alembic.config import Config
from alembic.script import ScriptDirectory
from pathlib import Path

from app.db.models import CodeChunkRow

from app.errors import ApplicationError
from app.main import create_app
from app.modules.retrieval.persistence import KeywordSearchStore
from app.modules.retrieval.schemas import (
    KeywordSearchRequest,
    KeywordSearchResult,
    KeywordSearchResponse,
)
from app.modules.retrieval.service import KeywordSearchService


def result(name, match_type, score, *, path="src/OrderService.java", chunk_id=None):
    return KeywordSearchResult(
        chunk_id=chunk_id or name, file_path=path, symbol_name=name,
        start_line=5, end_line=30, match_type=match_type, score=score,
    )


class FakeStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, repository_id, query, top_k):
        self.calls.append((repository_id, query, top_k))
        return self.results


def service(results):
    repository = SimpleNamespace(registered_record=lambda _id: None)
    store = FakeStore(results)
    return KeywordSearchService(repository, store), store, repository


def test_exact_symbol_and_qualified_name_results_are_preserved():
    repository_id = uuid4()
    search, store, _ = service([
        result("OrderService", "EXACT_SYMBOL", 1.0),
        result("createOrder", "EXACT_SYMBOL", 1.0),
    ])
    response = search.search(KeywordSearchRequest(
        repository_id=repository_id, query="  OrderService  ", top_k=2
    ))
    assert response.repository_id == repository_id
    assert response.results[0].match_type == "EXACT_SYMBOL"
    assert response.results[0].score == 1.0
    assert store.calls == [(repository_id, "OrderService", 2)]


def test_full_text_no_matches_and_missing_repository():
    repository_id = uuid4()
    search, store, repository = service([result("processPayment", "FULL_TEXT", 0.24)])
    response = search.search(KeywordSearchRequest(repository_id=repository_id, query="payment gateway"))
    assert response.results[0].score == 0.24
    store.results = []
    assert search.search(KeywordSearchRequest(repository_id=repository_id, query="unknown")).results == []
    store.results = None
    with pytest.raises(ApplicationError) as error:
        search.search(KeywordSearchRequest(repository_id=repository_id, query="unknown"))
    assert error.value.status_code == 404
    repository.registered_record = lambda _id: object()
    assert search.search(KeywordSearchRequest(repository_id=repository_id, query="unknown")).results == []


def test_database_failure_does_not_expose_connection_details():
    search, store, _ = service([])
    store.search = lambda *_args: (_ for _ in ()).throw(
        OperationalError("SELECT", {}, Exception("private database URL"))
    )
    with pytest.raises(ApplicationError) as error:
        search.search(KeywordSearchRequest(repository_id=uuid4(), query="OrderService"))
    assert error.value.status_code == 503
    assert "private database URL" not in error.value.message


@pytest.mark.parametrize("query,top_k", [
    ("", 5), ("   ", 5), ("x" * 2001, 5), ("OrderService", 0), ("OrderService", 21),
])
def test_request_validation(query, top_k):
    response = TestClient(create_app()).post("/api/v1/search/keyword", json={
        "repository_id": str(uuid4()), "query": query, "top_k": top_k,
    })
    assert response.status_code == 422


def test_sql_is_parameterized_scoped_and_deterministically_ranked():
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.get.return_value = object()
    session.execute.return_value.all.return_value = []
    store = KeywordSearchStore(factory)
    injection = "OrderService' OR 1=1 --"
    assert store.search(uuid4(), injection, 5) == []
    statement = session.execute.call_args_list[-1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert injection not in sql
    assert injection in compiled.params.values()
    assert "code_chunks.repository_id" in sql
    assert "code_chunks.symbol_name" in sql
    assert "code_chunks.qualified_name" in sql
    assert "code_chunks.parent_symbol" in sql
    assert "websearch_to_tsquery" in sql
    assert "ts_rank_cd" in sql
    assert "@@" in sql
    assert "ORDER BY" in sql and "start_byte" in sql and "chunk_id" in sql
    assert "LIMIT" in sql


def test_store_returns_database_calculated_rank_and_match_type():
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.get.return_value = object()
    row = ("a" * 64, "src/OrderService.java", "OrderService", 5, 30)
    session.execute.return_value.all.return_value = [
        (*row, "EXACT_SYMBOL", 1.0),
        (*row, "FULL_TEXT", 0.37),
    ]
    results = KeywordSearchStore(factory).search(uuid4(), "OrderService", 5)
    assert [item.score for item in results] == [1.0, 0.37]
    assert [item.match_type for item in results] == ["EXACT_SYMBOL", "FULL_TEXT"]


def test_openapi_and_http_success(monkeypatch):
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/search/keyword"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("KeywordSearchRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("KeywordSearchResponse")
    assert {"200", "404", "422", "503"}.issubset(operation["responses"])
    repository_id = uuid4()
    assert client.post("/api/v1/search/keyword", json={
        "repository_id": str(repository_id), "query": "OrderService"
    }).status_code == 503

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example.invalid/test_db")
    def fake_search(_self, request):
        responses = {
            "OrderService": [result("OrderService", "EXACT_SYMBOL", 1.0)],
            "processPayment": [result("processPayment", "EXACT_SYMBOL", 1.0)],
            "payment gateway": [result("processPayment", "FULL_TEXT", 0.37)],
        }
        return KeywordSearchResponse(
            repository_id=request.repository_id,
            results=responses.get(request.query, []),
        )

    monkeypatch.setattr(KeywordSearchService, "search", fake_search)
    app = create_app()
    try:
        configured = TestClient(app)
        for query, expected_type in (
            ("OrderService", "EXACT_SYMBOL"),
            ("processPayment", "EXACT_SYMBOL"),
            ("payment gateway", "FULL_TEXT"),
            ("tokenwithnomatches", None),
        ):
            response = configured.post("/api/v1/search/keyword", json={
                "repository_id": str(repository_id), "query": query, "top_k": 5,
            })
            assert response.status_code == 200
            matches = response.json()["results"]
            if expected_type:
                assert matches[0]["match_type"] == expected_type
            else:
                assert matches == []
    finally:
        app.state.db_engine.dispose()


def test_search_migration_and_generated_column():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20261103_0007"
    assert script.get_revision("20261030_0004").down_revision == "20261030_0003"
    column = CodeChunkRow.__table__.c.search_vector
    assert column.computed is not None
    assert "to_tsvector" in str(column.computed.sqltext)
    gin = next(index for index in CodeChunkRow.__table__.indexes
               if index.name == "ix_code_chunks_search_vector")
    assert gin.dialect_options["postgresql"]["using"] == "gin"
