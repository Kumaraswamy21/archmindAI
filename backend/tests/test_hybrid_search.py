"""Reciprocal-rank fusion and hybrid API behavior without cloud access."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.retrieval.fusion import fuse_rrf
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_persistence import ChunkIdentity, HybridChunkStore
from app.modules.retrieval.hybrid_schemas import HybridSearchRequest
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.schemas import KeywordSearchResponse, KeywordSearchResult
from app.modules.retrieval.service import KeywordSearchService
from app.modules.semantic_search.schemas import SemanticSearchResponse, SearchResult
from app.modules.semantic_search.service import SemanticSearchService


def identities(*ids, paths=None):
    paths = paths or {}
    return {
        chunk_id: ChunkIdentity(
            chunk_id, paths.get(chunk_id, f"src/{chunk_id}.java"),
            f"example.Service.{chunk_id}",
        )
        for chunk_id in ids
    }


def response(repository_id, ids):
    return SimpleNamespace(
        repository_id=repository_id,
        results=[SimpleNamespace(chunk_id=chunk_id) for chunk_id in ids],
    )


def hybrid_service(repository_id, semantic_ids, keyword_ids, *, settings=None):
    semantic = SimpleNamespace(search=MagicMock(return_value=response(repository_id, semantic_ids)))
    keyword = SimpleNamespace(search=MagicMock(return_value=response(repository_id, keyword_ids)))
    chunk_store = SimpleNamespace(
        get_identities=MagicMock(return_value=identities(*(set(semantic_ids) | set(keyword_ids))))
    )
    service = HybridSearchService(
        semantic, keyword, chunk_store, settings or HybridSearchSettings()
    )
    return service, semantic, keyword, chunk_store


def test_rrf_formula_deduplication_and_source_ranks():
    fused = fuse_rrf(
        ["both", "semantic-only"], ["keyword-only", "both"],
        identities("both", "semantic-only", "keyword-only"),
        rrf_constant=60, top_k=3,
    )
    assert [item.chunk_id for item in fused] == ["both", "keyword-only", "semantic-only"]
    both = fused[0]
    assert both.rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert both.retrieval_sources == ["semantic", "keyword"]
    assert both.source_ranks == {"semantic": 1, "keyword": 2}
    assert fused[1].rrf_score == pytest.approx(1 / 61)
    assert fused[1].retrieval_sources == ["keyword"]
    assert fused[2].retrieval_sources == ["semantic"]
    assert len({item.chunk_id for item in fused}) == 3


def test_configurable_constant_empty_lists_duplicate_rank_and_stable_ties():
    assert fuse_rrf([], [], {}, rrf_constant=60, top_k=5) == []
    fused = fuse_rrf(
        ["b", "b", "a"], [], identities("a", "b", paths={"a": "a.java", "b": "b.java"}),
        rrf_constant=10, top_k=2,
    )
    assert fused[0].source_ranks == {"semantic": 1}
    assert fused[0].rrf_score == pytest.approx(1 / 11)
    tied = fuse_rrf(
        ["b"], ["a"], identities("a", "b", paths={"a": "a.java", "b": "b.java"}),
        rrf_constant=60, top_k=2,
    )
    assert [item.chunk_id for item in tied] == ["a", "b"]
    with pytest.raises(ValueError):
        fuse_rrf([], [], {}, rrf_constant=0, top_k=1)


def test_service_uses_bounded_pool_and_returns_top_k():
    repository_id = uuid4()
    service, semantic, keyword, store = hybrid_service(
        repository_id, ["a", "b"], ["b", "c"],
        settings=HybridSearchSettings(hybrid_rrf_constant=10, hybrid_candidate_pool=3),
    )
    request = HybridSearchRequest(repository_id=repository_id, query="  How are orders processed?  ", top_k=2)
    result = service.search(request)
    assert result.query == "How are orders processed?"
    assert [item.chunk_id for item in result.results] == ["b", "a"]
    assert result.results[0].rrf_score == pytest.approx(1 / 12 + 1 / 11)
    assert semantic.search.call_args.args[0].top_k == 3
    assert keyword.search.call_args.args[0].top_k == 3
    assert semantic.search.call_args.args[0].repository_id == repository_id
    assert keyword.search.call_args.args[0].repository_id == repository_id
    assert store.get_identities.call_args.args[0] == repository_id
    assert store.get_identities.call_args.args[1] == {"a", "b", "c"}


def test_failure_is_not_returned_as_partial_result():
    repository_id = uuid4()
    service, semantic, keyword, store = hybrid_service(repository_id, ["a"], ["b"])
    request = HybridSearchRequest(repository_id=repository_id, query="orders")
    semantic.search.side_effect = ApplicationError(409, "EMBEDDINGS_NOT_AVAILABLE", "No compatible embeddings.")
    with pytest.raises(ApplicationError) as error:
        service.search(request)
    assert error.value.status_code == 409
    keyword.search.assert_not_called()
    semantic.search.side_effect = None
    keyword.search.side_effect = ApplicationError(503, "DATABASE_UNAVAILABLE", "Search storage unavailable.")
    with pytest.raises(ApplicationError) as error:
        service.search(request)
    assert error.value.status_code == 503
    store.get_identities.assert_not_called()


def test_repository_scope_checks_and_database_failure():
    repository_id = uuid4()
    request = HybridSearchRequest(repository_id=repository_id, query="orders")
    service, semantic, keyword, store = hybrid_service(repository_id, ["a"], ["b"])
    semantic.search.return_value.repository_id = uuid4()
    with pytest.raises(ApplicationError) as error:
        service.search(request)
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"
    semantic.search.return_value.repository_id = repository_id
    store.get_identities.return_value = identities("a")
    with pytest.raises(ApplicationError) as error:
        service.search(request)
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"
    store.get_identities.side_effect = OperationalError("SELECT", {}, Exception("private database URL"))
    with pytest.raises(ApplicationError) as error:
        service.search(request)
    assert error.value.status_code == 503
    assert "private database URL" not in error.value.message


@pytest.mark.parametrize("query,top_k", [
    ("", 5), ("   ", 5), ("x" * 2001, 5), ("orders", 0), ("orders", 21),
])
def test_request_validation(query, top_k):
    response = TestClient(create_app()).post("/api/v1/search/hybrid", json={
        "repository_id": str(uuid4()), "query": query, "top_k": top_k,
    })
    assert response.status_code == 422


def test_candidate_identity_query_is_repository_scoped_and_parameterized():
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.execute.return_value.all.return_value = []
    store = HybridChunkStore(factory)
    assert store.get_identities(uuid4(), set()) == {}
    first, second = "candidate-abc123", "candidate-def456"
    store.get_identities(uuid4(), {first, second})
    statement = session.execute.call_args_list[-1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "code_chunks.repository_id" in sql
    assert "code_chunks.chunk_id" in sql
    assert "IN" in sql
    assert first not in sql and second not in sql
    assert any(value == [first, second] for value in compiled.params.values())


def test_openapi_and_http_response(monkeypatch):
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/search/hybrid"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("HybridSearchRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("HybridSearchResponse")
    assert {"200", "404", "409", "422", "429", "502", "503", "504"}.issubset(operation["responses"])
    repository_id = uuid4()
    assert client.post("/api/v1/search/hybrid", json={
        "repository_id": str(repository_id), "query": "How are orders processed?"
    }).status_code == 503

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example.invalid/test_db")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(SemanticSearchService, "search", lambda _self, request: SemanticSearchResponse(
        repository_id=request.repository_id, query=request.query,
        results=[SearchResult(
            chunk_id=chunk_id, file_path=f"src/{chunk_id}.java", symbol_name=chunk_id,
            start_line=1, end_line=2, similarity_score=score, content="void run() {}",
        ) for chunk_id, score in (("a", 0.9), ("b", 0.4))],
    ))
    monkeypatch.setattr(KeywordSearchService, "search", lambda _self, request: KeywordSearchResponse(
        repository_id=request.repository_id,
        results=[KeywordSearchResult(
            chunk_id=chunk_id, file_path=f"src/{chunk_id}.java", symbol_name=chunk_id,
            start_line=1, end_line=2, match_type="EXACT_SYMBOL", score=score,
        ) for chunk_id, score in (("b", 1.0), ("c", 0.5))],
    ))
    monkeypatch.setattr(HybridChunkStore, "get_identities", lambda _self, _id, ids: identities(*ids))
    app = create_app()
    try:
        configured = TestClient(app)
        payload = {"repository_id": str(repository_id), "query": "How are orders processed?", "top_k": 2}
        semantic = configured.post("/api/v1/search/semantic", json=payload)
        keyword = configured.post("/api/v1/search/keyword", json=payload)
        response = configured.post("/api/v1/search/hybrid", json=payload)
        assert semantic.status_code == keyword.status_code == response.status_code == 200
        assert [row["chunk_id"] for row in semantic.json()["results"]] == ["a", "b"]
        assert [row["chunk_id"] for row in keyword.json()["results"]] == ["b", "c"]
        assert response.status_code == 200
        results = response.json()["results"]
        assert [row["chunk_id"] for row in results] == ["b", "a"]
        assert results[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
        assert results[0]["source_ranks"] == {"semantic": 2, "keyword": 1}
    finally:
        app.state.db_engine.dispose()
