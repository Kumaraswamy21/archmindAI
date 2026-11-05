"""Snapshot-pinned retrieval evaluation and metric correctness."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.code_intelligence.chunking.java_chunker import JavaAstChunker
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.evaluation.dataset_loader import DatasetLoader
from app.modules.evaluation.metrics import calculate_metrics
from app.modules.evaluation.schemas import DatasetQuestion, EvaluationDataset, EvaluationRequest
from app.modules.evaluation.service import RetrievalEvaluationService
from app.modules.evaluation.snapshot import snapshot_id


def chunk(repository_id, symbol):
    return JavaChunk(
        chunk_id=symbol.ljust(64, "0"), repository_id=repository_id,
        file_path="src/Example.java", language="java", chunk_type="method",
        start_byte=0 if symbol == "a" else 20,
        end_byte=10 if symbol == "a" else 30,
        symbol_name=symbol, qualified_name=f"example.Example.{symbol}",
        parent_symbol="Example", package_name="example", imports=[], annotations=[],
        start_line=2, end_line=3, content=f"void {symbol}() {{}}",
        contextual_header=f"Class: Example | Method: {symbol}",
    )


def evaluation_fixture():
    repository_id = uuid4()
    chunks = [chunk(repository_id, "a"), chunk(repository_id, "b")]
    dataset = EvaluationDataset(
        dataset_id="sample-v1", version="1.0.0",
        repository_snapshot_id=snapshot_id(chunks),
        questions=[
            DatasetQuestion(id="Q1", question="first?", expected_symbols=["example.Example.a", "example.Example.b"]),
            DatasetQuestion(id="Q2", question="second?", expected_symbols=["example.Example.b"]),
        ],
    )
    repository = SimpleNamespace(registered_record=lambda _id: None)
    chunk_store = SimpleNamespace(list_chunks=MagicMock(side_effect=lambda _id, limit, offset: (
        len(chunks), chunks[offset:offset + limit]
    )))
    loader = SimpleNamespace(load=MagicMock(return_value=dataset))
    rankings = {
        "semantic": {"first?": ["a"], "second?": []},
        "keyword": {"first?": ["b", "a"], "second?": ["b"]},
        "hybrid": {"first?": ["a", "b"], "second?": ["a", "b"]},
        "hybrid_reranked": {"first?": ["b", "a"], "second?": ["b"]},
    }
    services = []
    ids_by_symbol = {item.symbol_name: item.chunk_id for item in chunks}
    for name in ("semantic", "keyword", "hybrid", "hybrid_reranked"):
        def search(request, strategy=name):
            return SimpleNamespace(
                repository_id=request.repository_id,
                results=[SimpleNamespace(chunk_id=ids_by_symbol[symbol])
                         for symbol in rankings[strategy][request.query]],
            )
        services.append(SimpleNamespace(search=MagicMock(side_effect=search)))
    ticks = iter(index * 0.001 for index in range(100))
    evaluator = RetrievalEvaluationService(
        repository, chunk_store, *services, loader,
        "gemini-embedding-2", "metadata_heuristic_v1",
        clock=lambda: next(ticks),
    )
    return evaluator, repository_id, dataset, chunk_store, loader, services


def test_metric_formulas_multiple_relevant_and_missing_results():
    metrics = calculate_metrics(
        [{"a", "b"}, {"b"}],
        [["a", "x", "b"], []],
        [10.0, 30.0],
    )
    assert metrics.recall_at_5 == pytest.approx(0.5)
    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.average_latency_ms == pytest.approx(20.0)
    with pytest.raises(ValueError):
        calculate_metrics([], [], [])
    with pytest.raises(ValueError):
        calculate_metrics([set()], [[]], [1.0])


def test_versioned_dataset_matches_non_sensitive_sample_snapshot():
    dataset = DatasetLoader().load("java-code-search-v1")
    assert dataset.dataset_id == "java-code-search-v1"
    assert dataset.version == "1.0.0"
    assert 10 <= len(dataset.questions) <= 20
    source = Path(__file__).resolve().parents[1] / "evaluation/sample_repository/src/main/java/example/CommerceService.java"
    syntax = JavaParserAdapter().parse_tree(source.read_text())
    chunks = JavaAstChunker().chunk(
        uuid4(), "src/main/java/example/CommerceService.java", syntax,
        JavaSymbolExtractor().extract(syntax),
    )
    assert dataset.repository_snapshot_id == snapshot_id(chunks)
    assert all(set(item.expected_symbols) <= {chunk.qualified_name for chunk in chunks}
               for item in dataset.questions)


def test_all_strategies_use_same_dataset_and_actual_rankings():
    evaluator, repository_id, dataset, chunk_store, loader, services = evaluation_fixture()
    response = evaluator.evaluate(EvaluationRequest(
        repository_id=repository_id, dataset="sample-v1", top_k=5,
    ))
    assert response.dataset == dataset.dataset_id
    assert response.dataset_version == "1.0.0"
    assert response.repository_snapshot_id == dataset.repository_snapshot_id
    assert response.query_count == 2
    assert [item.strategy for item in response.strategies] == [
        "semantic", "keyword", "hybrid", "hybrid_reranked"
    ]
    assert [item.recall_at_5 for item in response.strategies] == pytest.approx([0.25, 1.0, 1.0, 1.0])
    assert [item.mrr for item in response.strategies] == pytest.approx([0.5, 1.0, 0.75, 1.0])
    assert all(item.average_latency_ms == pytest.approx(1.0) for item in response.strategies)
    assert response.embedding_model_identifier == "gemini-embedding-2"
    assert response.reranker_version == "metadata_heuristic_v1"
    assert loader.load.call_args.args == ("sample-v1",)
    assert chunk_store.list_chunks.call_count == 2  # before and after the benchmark
    assert all(service.search.call_count == 2 for service in services)
    assert all(call.args[0].repository_id == repository_id for service in services
               for call in service.search.call_args_list)
    assert not hasattr(chunk_store, "replace_file_chunks")


def test_invalid_dataset_empty_dataset_and_snapshot_mismatch():
    evaluator, repository_id, dataset, _, loader, services = evaluation_fixture()
    request = EvaluationRequest(repository_id=repository_id, dataset="sample-v1")
    from app.modules.evaluation.dataset_loader import DatasetNotFound
    loader.load.side_effect = DatasetNotFound()
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(request)
    assert error.value.status_code == 404
    loader.load.side_effect = None
    loader.load.return_value = dataset.model_copy(update={"questions": []})
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(request)
    assert error.value.code == "EMPTY_DATASET"
    loader.load.return_value = dataset.model_copy(update={"repository_snapshot_id": "sha256:" + "0" * 64})
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(request)
    assert error.value.code == "REPOSITORY_SNAPSHOT_MISMATCH"
    assert all(service.search.call_count == 0 for service in services)


def test_unknown_repository_scope_violation_and_database_failure():
    evaluator, repository_id, _, chunk_store, _, services = evaluation_fixture()
    request = EvaluationRequest(repository_id=repository_id, dataset="sample-v1")
    chunk_store.list_chunks.side_effect = lambda *_args: None
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(request)
    assert error.value.status_code == 404
    chunk_store.list_chunks.side_effect = OperationalError("SELECT", {}, Exception("private URL"))
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(request)
    assert error.value.status_code == 503
    assert "private URL" not in error.value.message

    evaluator, repository_id, _, _, _, services = evaluation_fixture()
    services[0].search.side_effect = lambda _request: SimpleNamespace(
        repository_id=uuid4(), results=[]
    )
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(EvaluationRequest(repository_id=repository_id, dataset="sample-v1"))
    assert error.value.code == "RETRIEVAL_SCOPE_MISMATCH"


def test_openapi_and_http_evaluation(monkeypatch):
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/v1/evaluations/retrieval"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("EvaluationRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("EvaluationResponse")
    assert {"200", "404", "409", "413", "422", "429", "502", "503", "504"}.issubset(operation["responses"])
    assert client.post("/api/v1/evaluations/retrieval", json={
        "repository_id": str(uuid4()), "dataset": "java-code-search-v1"
    }).status_code == 503
    assert client.post("/api/v1/evaluations/retrieval", json={
        "repository_id": str(uuid4()), "dataset": "../../secret"
    }).status_code == 422
    assert client.post("/api/v1/evaluations/retrieval", json={
        "repository_id": str(uuid4()), "dataset": "java-code-search-v1", "top_k": 4
    }).status_code == 422

    from app.modules.evaluation.service import RetrievalEvaluationService
    evaluator, repository_id, _, _, _, _ = evaluation_fixture()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example.invalid/test_db")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    original_evaluate = RetrievalEvaluationService.evaluate
    monkeypatch.setattr(RetrievalEvaluationService, "evaluate", lambda _self, request: original_evaluate(evaluator, request))
    app = create_app()
    try:
        response = TestClient(app).post("/api/v1/evaluations/retrieval", json={
            "repository_id": str(repository_id), "dataset": "sample-v1", "top_k": 5
        })
        assert response.status_code == 200
        body = response.json()
        assert body["query_count"] == 2
        assert [item["strategy"] for item in body["strategies"]] == [
            "semantic", "keyword", "hybrid", "hybrid_reranked"
        ]
        assert body["embedding_model_identifier"] == "gemini-embedding-2"
        assert body["reranker_version"] == "metadata_heuristic_v1"
    finally:
        app.state.db_engine.dispose()
