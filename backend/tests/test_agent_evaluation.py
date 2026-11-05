"""Pinned Java/Spring labels, agent metric arithmetic, and safe failures."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.observability import RecordingTracer, NoOpTracer, configure_tracer
from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.java_chunker import JavaAstChunker
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.evaluation.agent_service import AgentEvaluationService
from app.modules.evaluation.dataset_loader import DatasetLoader, DatasetNotFound
from app.modules.evaluation.metrics import calculate_agent_metrics
from app.modules.evaluation.schemas import (
    AgentDatasetQuestion,
    AgentEvaluationDataset,
    AgentEvaluationRequest,
    AgentQuestionResult,
)
from app.modules.evaluation.snapshot import snapshot_id


SAMPLE = Path(__file__).resolve().parents[1] / "evaluation" / "spring_sample_repository"


def sample_chunks():
    parser, symbols, chunker = (
        JavaParserAdapter(),
        JavaSymbolExtractor(),
        JavaAstChunker(),
    )
    chunks = []
    for path in sorted(SAMPLE.rglob("*.java")):
        syntax = parser.parse_tree(path.read_text())
        assert not syntax.tree.root_node.has_error
        chunks.extend(
            chunker.chunk(
                uuid4(),
                path.relative_to(SAMPLE).as_posix(),
                syntax,
                symbols.extract(syntax),
            )
        )
    return chunks


def test_spring_datasets_pin_actual_source_and_labels():
    chunks = sample_chunks()
    symbols = {item.qualified_name for item in chunks}
    search = DatasetLoader().load("java-spring-search-v1")
    agents = DatasetLoader().load_agent("java-spring-agent-v1")
    assert (
        search.repository_snapshot_id
        == agents.repository_snapshot_id
        == snapshot_id(chunks)
    )
    assert all(set(item.expected_symbols) <= symbols for item in search.questions)
    assert {item.category for item in agents.questions} == {
        "code_lookup",
        "method_explanation",
        "dependency_analysis",
        "architecture_analysis",
        "change_impact",
        "insufficient_evidence",
    }
    assert sum(item.expect_abstention for item in agents.questions) == 2


def test_agent_metric_formulas_include_failures_and_undefined_rates():
    questions = [
        AgentDatasetQuestion(
            id="A01",
            category="code_lookup",
            question="Where?",
            expected_intent="CODE_LOOKUP",
            expected_workflow="code_analysis",
            expect_abstention=False,
        ),
        AgentDatasetQuestion(
            id="A02",
            category="insufficient_evidence",
            question="Missing?",
            expected_intent="CODE_LOOKUP",
            expected_workflow="code_analysis",
            expect_abstention=True,
        ),
        AgentDatasetQuestion(
            id="A03",
            category="change_impact",
            question="Impact?",
            expected_intent="DEPENDENCY_ANALYSIS",
            expected_workflow="dependency_analysis",
            expect_abstention=False,
        ),
    ]
    results = [
        AgentQuestionResult(
            question_id="A01",
            category="code_lookup",
            status="COMPLETED",
            classified_intent="CODE_LOOKUP",
            selected_workflow="code_analysis",
            citation_count=2,
            valid_citation_count=1,
            supported_claim_count=1,
            unsupported_claim_count=1,
            latency_ms=10,
        ),
        AgentQuestionResult(
            question_id="A02",
            category="insufficient_evidence",
            status="COMPLETED",
            classified_intent="CODE_LOOKUP",
            selected_workflow="code_analysis",
            answer_status="INSUFFICIENT_EVIDENCE",
            citation_count=0,
            valid_citation_count=0,
            supported_claim_count=0,
            unsupported_claim_count=0,
            latency_ms=20,
        ),
        AgentQuestionResult(
            question_id="A03",
            category="change_impact",
            status="FAILED",
            error_code="PROVIDER_TIMEOUT",
            citation_count=0,
            valid_citation_count=0,
            supported_claim_count=0,
            unsupported_claim_count=0,
            latency_ms=30,
        ),
    ]
    metric = calculate_agent_metrics(questions, results)
    assert metric.intent_accuracy == pytest.approx(2 / 3)
    assert metric.routing_accuracy == pytest.approx(2 / 3)
    assert metric.workflow_completion_rate == pytest.approx(2 / 3)
    assert metric.workflow_failure_rate == pytest.approx(1 / 3)
    assert metric.average_end_to_end_latency_ms == 20
    assert metric.citation_validity_rate == 0.5
    assert metric.claim_support_rate == 0.5
    assert metric.unsupported_claim_rate == 0.5
    assert metric.correct_abstention_rate == 1
    with pytest.raises(ValueError):
        calculate_agent_metrics(questions, results[:2])
    no_evidence = calculate_agent_metrics(
        questions[:1],
        [
            results[0].model_copy(
                update={
                    "citation_count": 0,
                    "valid_citation_count": 0,
                    "supported_claim_count": 0,
                    "unsupported_claim_count": 0,
                }
            )
        ],
    )
    assert no_evidence.citation_validity_rate is None
    assert no_evidence.claim_support_rate is None
    assert no_evidence.correct_abstention_rate is None


def evaluation_fixture(failure=False):
    repository_id = uuid4()
    chunks = sample_chunks()
    dataset = AgentEvaluationDataset(
        dataset_id="test-agent-v1",
        version="1.0.0",
        repository_snapshot_id=snapshot_id(chunks),
        questions=[
            AgentDatasetQuestion(
                id="A01",
                category="code_lookup",
                question="Where is OrderController.placeOrder implemented?",
                expected_intent="CODE_LOOKUP",
                expected_workflow="code_analysis",
                expect_abstention=False,
            )
        ],
    )
    chunk_store = SimpleNamespace(
        list_chunks=lambda _id, limit, offset: (
            len(chunks),
            chunks[offset : offset + limit],
        )
    )
    evidence = SimpleNamespace(
        repository_id=repository_id,
        file_path="src/OrderController.java",
        start_line=1,
        end_line=5,
        content="private source",
    )
    answer = SimpleNamespace(
        repository_id=repository_id,
        workflow_metadata=SimpleNamespace(
            classified_intent="CODE_LOOKUP",
            selected_workflow="code_analysis",
            answer_status="ANSWERED",
        ),
        verification_status="PARTIALLY_VERIFIED",
        citations=[
            SimpleNamespace(
                evidence_id="E1", file_path=evidence.file_path, start_line=1, end_line=5
            )
        ],
        claim_verifications=[
            SimpleNamespace(status="CITATION_VERIFIED"),
            SimpleNamespace(status="INSUFFICIENT_EVIDENCE"),
        ],
        unsupported_claims=[SimpleNamespace(claim="unsupported private source")],
    )

    def analyze(_request, *, request_id):
        if failure:
            raise ApplicationError(504, "PROVIDER_TIMEOUT", "private source")
        return answer, {"E1": evidence}

    workflow = SimpleNamespace(analyze_with_evidence=analyze)
    evaluator = AgentEvaluationService(
        SimpleNamespace(registered_record=lambda _id: object()),
        chunk_store,
        workflow,
        SimpleNamespace(load_agent=lambda _id: dataset),
        "embedding-test",
        "generation-test",
        "metadata_heuristic_v1",
        clock=iter([1.0, 1.012]).__next__,
    )
    return evaluator, repository_id, dataset


def test_agent_evaluation_reproducibility_and_unsupported_claims():
    tracer = RecordingTracer()
    configure_tracer(tracer)
    try:
        evaluator, repository_id, dataset = evaluation_fixture()
        response = evaluator.evaluate(
            AgentEvaluationRequest(
                repository_id=repository_id, dataset=dataset.dataset_id
            )
        )
        assert response.repository_snapshot_id == dataset.repository_snapshot_id
        assert response.embedding_model_identifier == "embedding-test"
        assert response.generation_model_identifier == "generation-test"
        assert response.retrieval_configuration == {
            "top_k": 5,
            "rrf_constant": 60,
            "candidate_pool": 20,
        }
        assert response.prompt_versions["code_analysis"] == {
            "system": "1.1.0",
            "user": "1.1.0",
        }
        assert response.metrics.citation_validity_rate == 1
        assert response.metrics.claim_support_rate == 0.5
        assert response.metrics.unsupported_claim_rate == 0.5
        assert response.questions[0].latency_ms == pytest.approx(12)
        metadata = json.dumps([span.metadata for span in tracer.spans])
        assert (
            dataset.dataset_id in metadata
            and dataset.repository_snapshot_id in metadata
        )
        assert "private source" not in metadata
    finally:
        configure_tracer(NoOpTracer())


def test_agent_evaluation_records_failure_without_source_content():
    evaluator, repository_id, dataset = evaluation_fixture(failure=True)
    response = evaluator.evaluate(
        AgentEvaluationRequest(repository_id=repository_id, dataset=dataset.dataset_id)
    )
    assert response.metrics.workflow_failure_rate == 1
    assert response.questions[0].error_code == "PROVIDER_TIMEOUT"
    assert "private source" not in response.model_dump_json()
    evaluator._loader.load_agent = lambda _id: (_ for _ in ()).throw(DatasetNotFound())
    with pytest.raises(ApplicationError) as error:
        evaluator.evaluate(
            AgentEvaluationRequest(
                repository_id=repository_id, dataset=dataset.dataset_id
            )
        )
    assert error.value.code == "DATASET_NOT_FOUND"
