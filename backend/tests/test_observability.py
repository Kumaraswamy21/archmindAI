"""Langfuse tracing hierarchy, redaction, disablement, and fail-open behavior."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.observability import (
    NoOpTracer, ObservabilitySettings, RecordingTracer, configure_from_settings,
    configure_tracer, filter_metadata, get_tracer, question_fingerprint, request_trace,
)
from app.core.observability.client import create_langfuse_client
from app.core.observability.tracer import LangfuseTracer
from app.errors import ApplicationError
from app.main import create_app
from app.modules.agentic.classifier import CLASSIFIER_VERSION, IntentClassification, IntentClassifier
from app.modules.agentic.evidence_verification import EvidenceVerifier
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from app.modules.rag.llm_provider import GeminiAnswerProvider, RagProviderError
from app.modules.rag.schemas import Citation, GeneratedAnswer
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_persistence import ChunkIdentity
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.rerank_persistence import RerankMetadata
from app.modules.retrieval.rerank_schemas import RerankRequest
from app.modules.retrieval.rerank_service import RerankSearchService
from app.modules.retrieval.reranker import MetadataHeuristicV1
from app.prompts.loader import PromptLoader
from test_agentic import graph_stub, workflow_for  # noqa: F401
from test_evidence_verification import analysis as verification_analysis


@pytest.fixture(autouse=True)
def reset_tracer():
    configure_tracer(NoOpTracer())
    yield
    configure_tracer(NoOpTracer())


def _dump(tracer: RecordingTracer) -> str:
    return json.dumps([
        {
            "name": span.name, "parent_id": span.parent_id, "as_type": span.as_type,
            "metadata": span.metadata, "usage": span.usage, "output": span.output,
            "session_id": span.session_id, "model": span.model, "error_code": span.error_code,
        }
        for span in tracer.spans
    ], default=str)


def test_question_fingerprint_and_metadata_allowlist():
    fingerprint = question_fingerprint("Where is OrderService?")
    assert fingerprint["question_length"] == len("Where is OrderService?")
    assert len(fingerprint["question_sha256"]) == 64
    assert "Where is OrderService?" not in fingerprint.values()
    filtered = filter_metadata({
        "request_id": "abc", "question": "secret", "content": "class Foo {}",
        "GEMINI_API_KEY": "abc", "api_key": "abc", "system_prompt": "hidden",
        "generation_model": "gemini-2.5-flash", "latency_ms": 12.5,
        **fingerprint,
    })
    assert filtered["request_id"] == "abc"
    assert filtered["generation_model"] == "gemini-2.5-flash"
    assert filtered["question_length"] == fingerprint["question_length"]
    assert "question" not in filtered
    assert "content" not in filtered
    assert "api_key" not in filtered
    assert "GEMINI_API_KEY" not in filtered


def test_disabled_and_missing_keys_create_no_client(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert create_langfuse_client(ObservabilitySettings(langfuse_enabled=False)) is None
    assert create_langfuse_client(ObservabilitySettings(langfuse_enabled=True)) is None
    tracer = configure_from_settings(ObservabilitySettings(langfuse_enabled=True))
    assert isinstance(tracer, NoOpTracer)
    assert get_tracer().enabled() is False


def test_chat_trace_hierarchy_request_id_and_redaction(graph_stub):
    tracer = RecordingTracer()
    configure_tracer(tracer)
    repository_id = uuid4()
    request_id = uuid4()
    question = "Where is OrderService implemented?"
    workflow, *_ = workflow_for(repository_id)
    response = ChatService(workflow).respond(
        ChatRequest(repository_id=repository_id, message=question),
        request_id=request_id,
    )
    assert response.classified_intent == "CODE_LOOKUP"
    names = [span.name for span in tracer.spans]
    assert names[0] == "chat"
    assert "intent_classification" in names
    assert "agent.code_analysis" in names
    assert "evidence_verification" in names
    root = tracer.spans[0]
    assert root.metadata["request_id"] == str(request_id)
    assert root.metadata["repository_id"] == str(repository_id)
    assert root.metadata["workflow_type"] == "chat"
    by_id = {span.id: span for span in tracer.spans}
    for span in tracer.spans[1:]:
        assert span.parent_id is not None
        assert span.parent_id in by_id
    payload = _dump(tracer)
    assert question not in payload
    assert "class OrderService" not in payload
    assert "GEMINI_API_KEY" not in payload
    intent = next(span for span in tracer.spans if span.name == "intent_classification")
    assert intent.metadata["classifier"] == "rules"


def test_llm_classifier_records_prompt_versions():
    tracer = RecordingTracer()
    configure_tracer(tracer)
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate_structured=MagicMock(return_value=IntentClassification(
            intent="UNKNOWN", routing_reason="Question is too vague.",
        )),
    )
    IntentClassifier(PromptLoader(), provider).classify("Could you look at this?")
    span = next(item for item in tracer.spans if item.name == "intent_classification")
    assert span.as_type == "generation"
    assert span.model == "gemini-2.5-flash"
    assert span.metadata["prompt_name"] == "query_classifier"
    assert span.metadata["system_prompt_version"]
    assert span.metadata["user_prompt_template_version"]
    assert span.metadata["generation_model"] == "gemini-2.5-flash"


def test_retrieval_rerank_generation_verification_nesting():
    tracer = RecordingTracer()
    configure_tracer(tracer)
    repository_id = uuid4()
    chunk_id = "processPayment"
    hybrid = HybridSearchService(
        SimpleNamespace(search=MagicMock(return_value=SimpleNamespace(
            repository_id=repository_id, results=[SimpleNamespace(chunk_id=chunk_id)],
        ))),
        SimpleNamespace(search=MagicMock(return_value=SimpleNamespace(
            repository_id=repository_id, results=[SimpleNamespace(chunk_id=chunk_id)],
        ))),
        SimpleNamespace(get_identities=MagicMock(return_value={
            chunk_id: ChunkIdentity(chunk_id, "src/processPayment.java", "example.PaymentService.processPayment"),
        })),
        HybridSearchSettings(),
    )
    rerank = RerankSearchService(
        hybrid,
        SimpleNamespace(get_many=MagicMock(return_value={
            chunk_id: RerankMetadata(
                chunk_id, "src/processPayment.java",
                "example.PaymentService.processPayment", "processPayment", "",
            ),
        })),
        MetadataHeuristicV1(),
        HybridSearchSettings(),
    )
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        parsed=GeneratedAnswer(
            answer="The source defines processPayment; runtime behavior is not shown.",
            citations=[Citation(
                evidence_id="E1", file_path="src/processPayment.java", start_line=5, end_line=10,
            )],
            confidence="HIGH", limitations=[],
        ).model_dump(),
        usage_metadata=SimpleNamespace(
            prompt_token_count=11, candidates_token_count=7, total_token_count=18,
        ),
        text=None,
    )
    provider = GeminiAnswerProvider(
        api_key="test-only", model_identifier="gemini-2.5-flash",
        timeout_seconds=5, client=client,
    )
    with request_trace("chat", workflow_type="chat", repository_id=repository_id, request_id="req-1"):
        with get_tracer().span("intent_classification"):
            pass
        with get_tracer().span("agent.code_analysis", as_type="agent"):
            rerank.search(RerankRequest(repository_id=repository_id, query="processPayment", top_k=1))
            provider.generate("hidden system prompt", "hidden user prompt")
        EvidenceVerifier().verify(repository_id, verification_analysis(repository_id))
    names = [span.name for span in tracer.spans]
    assert names[0] == "chat"
    assert "intent_classification" in names
    assert "retrieval" in names
    assert "reranking" in names
    assert "agent.code_analysis" in names
    assert "gemini.generation" in names
    assert "evidence_verification" in names
    generation = next(span for span in tracer.spans if span.name == "gemini.generation")
    assert generation.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert generation.model == "gemini-2.5-flash"
    payload = _dump(tracer)
    assert "hidden system prompt" not in payload
    assert "hidden user prompt" not in payload
    assert "class OrderService" not in payload


def test_generation_does_not_invent_token_counts():
    tracer = RecordingTracer()
    configure_tracer(tracer)
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        parsed=GeneratedAnswer(
            answer="ok", citations=[], confidence="INSUFFICIENT", limitations=[],
        ).model_dump(),
        usage_metadata=None, text=None,
    )
    GeminiAnswerProvider(
        api_key="test-only", model_identifier="gemini-2.5-flash",
        timeout_seconds=5, client=client,
    ).generate("system", "user")
    generation = next(span for span in tracer.spans if span.name == "gemini.generation")
    assert generation.usage is None


def test_error_category_is_recorded_and_request_still_fails(graph_stub):
    tracer = RecordingTracer()
    configure_tracer(tracer)
    repository_id = uuid4()
    workflow, code, *_ = workflow_for(repository_id)
    code.answer.side_effect = ApplicationError(502, "RAG_PROVIDER_FAILED", "Analysis generation failed.")
    with pytest.raises(ApplicationError) as error:
        ChatService(workflow).respond(
            ChatRequest(repository_id=repository_id, message="Explain OrderService."),
        )
    assert error.value.code == "RAG_PROVIDER_FAILED"
    root = tracer.spans[0]
    assert root.error_code == "RAG_PROVIDER_FAILED"
    assert root.metadata["error_category"] == "RAG_PROVIDER_FAILED"


def test_langfuse_sdk_failures_do_not_break_answers():
    class BrokenClient:
        def start_as_current_observation(self, **_kwargs):
            raise RuntimeError("langfuse unavailable")

        def flush(self):
            raise RuntimeError("flush failed")

    configure_tracer(LangfuseTracer(BrokenClient(), 2.0))
    decision = IntentClassifier(PromptLoader(), SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate_structured=MagicMock(return_value=IntentClassification(
            intent="UNKNOWN", routing_reason="Question is too vague.",
            classifier_version=CLASSIFIER_VERSION,
        )),
    )).classify("Could you look at this?")
    assert decision.classification.intent == "UNKNOWN"
    get_tracer().flush()


def test_existing_chat_and_docs_unaffected_when_disabled():
    client = TestClient(create_app())
    assert client.get("/api/v1/health").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/api/v1/chat" in spec["paths"]
    assert "/api/v1/ask" in spec["paths"]
    assert "/api/v1/agents/analyze" in spec["paths"]
    assert not any("langfuse" in path for path in spec["paths"])
    assert isinstance(get_tracer(), NoOpTracer)


def test_provider_error_records_code_without_prompt_text():
    tracer = RecordingTracer()
    configure_tracer(tracer)
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("upstream secret")
    with pytest.raises(RagProviderError):
        GeminiAnswerProvider(
            api_key="test-only", model_identifier="gemini-2.5-flash",
            timeout_seconds=5, client=client,
        ).generate("system secret prompt", "user secret prompt")
    payload = _dump(tracer)
    assert "system secret prompt" not in payload
    assert "user secret prompt" not in payload
    assert "upstream secret" not in payload
    assert tracer.spans[0].error_code == "RAG_PROVIDER_FAILED"
