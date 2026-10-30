"""Semantic search contracts and repository isolation without cloud access."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.semantic_search.persistence import RankedChunk, SemanticSearchStore
from app.modules.semantic_search.schemas import SearchResult, SemanticSearchRequest
from app.modules.semantic_search.service import SemanticSearchService
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.embeddings.base import EmbeddingDimensionError, EmbeddingTimeoutError
from app.modules.vector_store.input_builder import EMBEDDING_INPUT_VERSION


def chunk(repository_id, name, *, file_path="Source.java", start_byte=0):
    return JavaChunk(
        chunk_id=name.ljust(64, "0"), repository_id=repository_id,
        file_path=file_path, language="java", chunk_type="method",
        start_byte=start_byte, end_byte=start_byte + 10,
        symbol_name=name, qualified_name=f"Source.{name}", parent_symbol="Source",
        package_name=None, imports=[], annotations=[], start_line=2, end_line=4,
        content=f"void {name}() {{}}", contextual_header=f"Method: {name}",
    )


class Chunks:
    def __init__(self, chunks, *, found=True):
        self.chunks = chunks
        self.found = found

    def list_chunks(self, repository_id, limit, offset):
        if not self.found:
            return None
        selected = [item for item in self.chunks if item.repository_id == repository_id]
        return len(selected), selected[offset:offset + limit]


class Search:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def search_page(self, repository_id, hashes, vector, model, dimension, version, top_k):
        self.calls.append((repository_id, hashes, vector, model, dimension, version, top_k))
        assert version == EMBEDDING_INPUT_VERSION
        return sorted(
            [
                RankedChunk(
                    result=SearchResult(
                        chunk_id=item.chunk_id, file_path=item.file_path,
                        symbol_name=item.symbol_name, start_line=item.start_line,
                        end_line=item.end_line, similarity_score=score,
                        content=item.content,
                    ),
                    start_byte=item.start_byte,
                )
                for item, score in self.scores if item.repository_id == repository_id
                and item.chunk_id in hashes
            ],
            key=lambda item: -item.result.similarity_score,
        )[:top_k]


def service(chunks, scores, *, found=True, provider=None):
    embedding = provider or SimpleNamespace(
        model_identifier="gemini-embedding-2", output_dimension=128,
        embed_query=lambda _query: [0.1] * 128,
    )
    search_store = Search(scores)
    return SemanticSearchService(
        SimpleNamespace(registered_record=lambda _id: None),
        Chunks(chunks, found=found), search_store, embedding,
        EmbeddingIndexSettings(embedding_dimension=128),
    ), search_store


def test_ranking_top_k_ties_and_repository_filtering():
    repository_id, other_id = uuid4(), uuid4()
    items = [chunk(repository_id, "payment", file_path="b.java"),
             chunk(repository_id, "charge", file_path="a.java"),
             chunk(repository_id, "refund", file_path="c.java"),
             chunk(other_id, "secret", file_path="secret.java")]
    search, store = service(items, list(zip(items, [0.8, 0.8, 0.2, 0.99])))
    result = search.search(SemanticSearchRequest(
        repository_id=repository_id, query="  Where is payment processing implemented?  ", top_k=2
    ))
    assert result.query == "Where is payment processing implemented?"
    assert [item.symbol_name for item in result.results] == ["charge", "payment"]
    assert [item.similarity_score for item in result.results] == [0.8, 0.8]
    assert all(call[0] == repository_id for call in store.calls)
    assert all(items[-1].chunk_id not in call[1] for call in store.calls)


def test_pagination_merges_global_top_k():
    repository_id = uuid4()
    items = [chunk(repository_id, f"m{i}", start_byte=i * 10) for i in range(101)]
    scores = [(item, 0.1) for item in items]
    scores[-1] = (items[-1], 0.99)
    search, store = service(items, scores)
    result = search.search(SemanticSearchRequest(repository_id=repository_id, query="last", top_k=1))
    assert result.results[0].symbol_name == "m100"
    assert len(store.calls) == 2


@pytest.mark.parametrize("query,top_k", [("", 5), ("   ", 5), ("x" * 2001, 5), ("valid", 0), ("valid", 21)])
def test_validation_rejects_invalid_requests(query, top_k):
    response = TestClient(create_app()).post("/api/v1/search/semantic", json={
        "repository_id": str(uuid4()), "query": query, "top_k": top_k,
    })
    assert response.status_code == 422


def test_missing_repository_no_embeddings_and_mismatch():
    repository_id = uuid4()
    request = SemanticSearchRequest(repository_id=repository_id, query="where?")
    missing, _ = service([], [], found=False)
    with pytest.raises(ApplicationError) as error:
        missing.search(request)
    assert error.value.status_code == 404
    missing._repository_service.registered_record = lambda _id: object()
    with pytest.raises(ApplicationError) as error:
        missing.search(request)
    assert error.value.code == "EMBEDDINGS_NOT_AVAILABLE"
    empty, _ = service([], [])
    with pytest.raises(ApplicationError) as error:
        empty.search(request)
    assert error.value.code == "EMBEDDINGS_NOT_AVAILABLE"
    item = chunk(repository_id, "method")
    no_vectors, _ = service([item], [])
    with pytest.raises(ApplicationError) as error:
        no_vectors.search(request)
    assert error.value.status_code == 409
    wrong_provider = SimpleNamespace(model_identifier="other", output_dimension=128, embed_query=lambda _: [0.1] * 128)
    mismatch, _ = service([item], [], provider=wrong_provider)
    with pytest.raises(ApplicationError) as error:
        mismatch.search(request)
    assert error.value.code == "EMBEDDING_CONFIG_MISMATCH"


def test_provider_and_database_failures_are_controlled():
    repository_id = uuid4()
    item = chunk(repository_id, "method")
    request = SemanticSearchRequest(repository_id=repository_id, query="where?")
    provider = SimpleNamespace(model_identifier="gemini-embedding-2", output_dimension=128,
                               embed_query=lambda _: (_ for _ in ()).throw(EmbeddingTimeoutError()))
    search, _ = service([item], [], provider=provider)
    with pytest.raises(ApplicationError) as error:
        search.search(request)
    assert error.value.status_code == 504
    provider.embed_query = lambda _: [0.1] * 127
    with pytest.raises(ApplicationError) as error:
        search.search(request)
    assert error.value.code == EmbeddingDimensionError.code
    search, store = service([item], [])
    store.search_page = lambda *_: (_ for _ in ()).throw(
        OperationalError("SELECT", {}, Exception("private database URL"))
    )
    with pytest.raises(ApplicationError) as error:
        search.search(request)
    assert error.value.status_code == 503
    assert "private database URL" not in error.value.message


def test_openapi_and_http_success(monkeypatch):
    from app.modules.semantic_search.service import SemanticSearchService

    repository_id = uuid4()
    client = TestClient(create_app())
    unconfigured = client.post("/api/v1/search/semantic", json={
        "repository_id": str(repository_id), "query": "payment"
    })
    assert unconfigured.status_code == 503
    spec = client.get("/openapi.json").json()
    endpoint = spec["paths"]["/api/v1/search/semantic"]["post"]
    assert endpoint["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("SemanticSearchRequest")
    assert endpoint["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("SemanticSearchResponse")
    assert {"404", "409", "429", "502", "503", "504"}.issubset(endpoint["responses"])
    assert client.get("/docs").status_code == 200

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example.invalid/test_db")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(SemanticSearchService, "search", lambda _self, request: {
        "repository_id": request.repository_id, "query": request.query,
        "results": [{"chunk_id": "abc", "file_path": "PaymentService.java",
                     "symbol_name": "processPayment", "start_line": 15, "end_line": 30,
                     "similarity_score": 0.89, "content": "void processPayment() {}"}],
    })
    app = create_app()
    try:
        response = TestClient(app).post("/api/v1/search/semantic", json={
            "repository_id": str(repository_id), "query": "Where is payment processing implemented?", "top_k": 5
        })
        assert response.status_code == 200
        assert response.json()["results"][0]["similarity_score"] == 0.89
    finally:
        app.state.db_engine.dispose()


def test_sql_uses_pgvector_and_repository_model_hash_filters():
    from unittest.mock import MagicMock

    factory = MagicMock()
    factory.return_value.__enter__.return_value.execute.return_value.all.return_value = []
    store = SemanticSearchStore(factory)
    store.search_page(uuid4(), {"a" * 64: "b" * 64}, [0.1] * 128,
                      "gemini-embedding-2", 128, "1.0.0", 5)
    session = factory.return_value.__enter__.return_value
    statement = session.execute.call_args_list[-1].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "<=>" in sql
    assert "code_chunks.repository_id" in sql
    assert "chunk_embeddings.model_identifier" in sql
    assert "chunk_embeddings.output_dimension" in sql
    assert "chunk_embeddings.input_hash" in sql
    assert "ORDER BY distance" in sql
    assert "LIMIT" in sql


def test_labeled_evaluation_metrics(monkeypatch):
    from evaluation import evaluate_semantic_search as evaluation

    expected = ["processPayment", "createOrder", "reserveStock"]
    returned = [
        ["processPayment"],
        ["unrelated", "createOrder"],
        ["unrelated"],
    ]

    class Client:
        def __init__(self, **_kwargs):
            self.index = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _path, *, json):
            assert json["query"]
            assert json["top_k"] == 5
            names = returned[self.index]
            self.index += 1
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"results": [{"symbol_name": name} for name in names]},
            )

    monkeypatch.setattr(evaluation.httpx, "Client", Client)
    metrics = evaluation.evaluate(uuid4(), "http://localhost:8000")
    assert metrics["questions"] == len(expected)
    assert metrics["recall_at_5"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert metrics["average_query_latency_ms"] >= 0
