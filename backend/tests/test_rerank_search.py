"""Metadata heuristic, repository-scoped lookup, and rerank HTTP contract."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_schemas import HybridSearchResponse, HybridSearchResult
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.rerank_persistence import RerankMetadata, RerankMetadataStore
from app.modules.retrieval.rerank_schemas import RerankRequest
from app.modules.retrieval.rerank_service import RerankSearchService
from app.modules.retrieval.reranker import MetadataHeuristicV1, RerankCandidate


def candidate(name, *, rank=1, qualified=None, header="", path=None):
    return RerankCandidate(
        chunk_id=name, file_path=path or f"src/{name}.java",
        qualified_name=qualified or f"example.Service.{name}",
        symbol_name=name, contextual_header=header, original_rank=rank,
    )


def hybrid_result(name, *, qualified=None, path=None):
    return HybridSearchResult(
        chunk_id=name, file_path=path or f"src/{name}.java",
        qualified_name=qualified or f"example.Service.{name}",
        rrf_score=0.01, retrieval_sources=["semantic"],
        source_ranks={"semantic": 1},
    )


def service(repository_id, names, metadata=None, *, pool=3):
    hybrid = SimpleNamespace(search=MagicMock(return_value=HybridSearchResponse(
        repository_id=repository_id, query="query",
        results=[hybrid_result(name) for name in names],
    )))
    metadata = metadata if metadata is not None else {
        name: RerankMetadata(
            name, f"src/{name}.java", f"example.Service.{name}", name, ""
        )
        for name in names
    }
    store = SimpleNamespace(get_many=MagicMock(return_value=metadata))
    rerank = RerankSearchService(
        hybrid, store, MetadataHeuristicV1(),
        HybridSearchSettings(hybrid_candidate_pool=pool),
    )
    return rerank, hybrid, store


def test_exact_symbol_outweighs_original_hybrid_rank():
    reranker = MetadataHeuristicV1()
    ranked = reranker.rerank("Where is processPayment implemented?", [
        candidate("unrelated", rank=1, header="Where is implemented"),
        candidate("processPayment", rank=2, header="Method: processPayment"),
    ])
    assert [row.chunk_id for row in ranked] == ["processPayment", "unrelated"]
    assert [(row.original_rank, row.rerankerank) for row in ranked] == [(2, 1), (1, 2)]
    assert all(row.reranker_version == "metadata_heuristic_v1" for row in ranked)


def test_qualified_name_metadata_overlap_and_calculated_score():
    reranker = MetadataHeuristicV1()
    first = candidate("processPayment", rank=2, header="Class: PaymentService | Method: processPayment")
    query = "Where is payment processing implemented?"
    ranked = reranker.rerank(query, [first])
    # Query terms: where, is, payment, processing, implemented. Only payment
    # overlaps the structural fields and header; original rank contributes 1/2.
    assert ranked[0].rerank_score == pytest.approx(2 / 5 + 1 / 5 + 1 / 2)
    qualified = reranker.rerank("example.Service.processPayment", [first])[0]
    assert qualified.rerank_score >= 5.0
    assert reranker.rerank("anything", []) == []


def test_stable_ties_and_deterministic_scores():
    reranker = MetadataHeuristicV1()
    items = [
        candidate("b", rank=1, path="b.java"),
        candidate("a", rank=1, path="a.java"),
    ]
    first = reranker.rerank("unmatched", items)
    second = reranker.rerank("unmatched", list(reversed(items)))
    assert [row.chunk_id for row in first] == ["a", "b"]
    assert [(row.chunk_id, row.rerank_score) for row in first] == [
        (row.chunk_id, row.rerank_score) for row in second
    ]
    with pytest.raises(ValueError):
        reranker.rerank("query", [candidate("invalid", rank=0)])


def test_service_reranks_bounded_hybrid_pool_and_slices_top_k():
    repository_id = uuid4()
    rerank, hybrid, store = service(repository_id, ["unrelated", "processPayment"])
    response = rerank.search(RerankRequest(
        repository_id=repository_id, query="  Where is processPayment implemented?  ",
        top_k=1,
    ))
    assert response.reranker == "metadata_heuristic_v1"
    assert [item.chunk_id for item in response.results] == ["processPayment"]
    assert response.results[0].original_rank == 2
    assert response.results[0].rerankerank == 1
    assert hybrid.search.call_args.args[0].top_k == 3
    assert hybrid.search.call_args.args[0].repository_id == repository_id
    assert store.get_many.call_args.args[0] == repository_id
    assert store.get_many.call_args.args[1] == {"unrelated", "processPayment"}


def test_empty_candidates_failure_propagation_and_repository_scope():
    repository_id = uuid4()
    request = RerankRequest(repository_id=repository_id, query="payment")
    rerank, hybrid, store = service(repository_id, [])
    assert rerank.search(request).results == []
    store.get_many.assert_called_with(repository_id, set())
    hybrid.search.side_effect = ApplicationError(409, "EMBEDDINGS_NOT_AVAILABLE", "No embeddings.")
    with pytest.raises(ApplicationError) as error:
        rerank.search(request)
    assert error.value.status_code == 409

    rerank, hybrid, store = service(repository_id, ["payment"])
    hybrid.search.return_value.repository_id = uuid4()
    with pytest.raises(ApplicationError) as error:
        rerank.search(request)
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"
    hybrid.search.return_value.repository_id = repository_id
    store.get_many.return_value = {}
    with pytest.raises(ApplicationError) as error:
        rerank.search(request)
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"
    store.get_many.return_value = {"payment": RerankMetadata(
        "payment", "other/path.java", "example.Service.payment", "payment", ""
    )}
    with pytest.raises(ApplicationError) as error:
        rerank.search(request)
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"


def test_database_failure_is_controlled():
    repository_id = uuid4()
    rerank, _, store = service(repository_id, ["payment"])
    store.get_many.side_effect = OperationalError("SELECT", {}, Exception("private URL"))
    with pytest.raises(ApplicationError) as error:
        rerank.search(RerankRequest(repository_id=repository_id, query="payment"))
    assert error.value.status_code == 503
    assert "private URL" not in error.value.message


@pytest.mark.parametrize("query,top_k", [
    ("", 5), ("  ", 5), ("x" * 2001, 5), ("payment", 0), ("payment", 21),
])
def test_request_validation(query, top_k):
    response = TestClient(create_app()).post("/api/v1/search/rerank", json={
        "repository_id": str(uuid4()), "query": query, "top_k": top_k,
    })
    assert response.status_code == 422


def test_metadata_query_is_repository_scoped_and_parameterized():
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.execute.return_value.all.return_value = []
    store = RerankMetadataStore(factory)
    assert store.get_many(uuid4(), set()) == {}
    name = "candidate-abc123"
    store.get_many(uuid4(), {name})
    statement = session.execute.call_args_list[-1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "code_chunks.repository_id" in sql
    assert "code_chunks.chunk_id" in sql
    assert "contextual_header" in sql
    assert name not in sql
    assert [name] in compiled.params.values()


def test_openapi_and_http_comparison(monkeypatch):
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/search/rerank"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("RerankRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("RerankResponse")
    assert {"200", "404", "409", "422", "429", "502", "503", "504"}.issubset(operation["responses"])
    repository_id = uuid4()
    assert client.post("/api/v1/search/rerank", json={
        "repository_id": str(repository_id), "query": "processPayment"
    }).status_code == 503

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example.invalid/test_db")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setattr(HybridSearchService, "search", lambda _self, request: HybridSearchResponse(
        repository_id=request.repository_id, query=request.query,
        results=[hybrid_result("unrelated"), hybrid_result("processPayment")],
    ))
    monkeypatch.setattr(RerankMetadataStore, "get_many", lambda _self, _id, ids: {
        name: RerankMetadata(
            name, f"src/{name}.java", f"example.Service.{name}", name, ""
        ) for name in ids
    })
    app = create_app()
    try:
        configured = TestClient(app)
        payload = {"repository_id": str(repository_id), "query": "Where is processPayment implemented?", "top_k": 2}
        hybrid = configured.post("/api/v1/search/hybrid", json=payload)
        reranked = configured.post("/api/v1/search/rerank", json=payload)
        assert hybrid.status_code == reranked.status_code == 200
        assert [row["chunk_id"] for row in hybrid.json()["results"]] == ["unrelated", "processPayment"]
        assert [row["chunk_id"] for row in reranked.json()["results"]] == ["processPayment", "unrelated"]
        assert reranked.json()["reranker"] == "metadata_heuristic_v1"
        assert [(row["original_rank"], row["rerankerank"]) for row in reranked.json()["results"]] == [(2, 1), (1, 2)]
        assert reranked.json()["results"][0]["rerank_score"] > reranked.json()["results"][1]["rerank_score"]
    finally:
        app.state.db_engine.dispose()
