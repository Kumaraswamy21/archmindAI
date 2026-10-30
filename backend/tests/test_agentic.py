"""Specialist routing, grounded metadata, controlled failures, and API schema."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import ApplicationError, handle_application_error
from app.main import create_app
from app.modules.agentic.analysis_common import AgentEvidence, AgentResult
from app.modules.agentic.classifier import IntentClassifier, IntentClassification
from app.modules.agentic.code_analysis import CodeAnalysisAgent
from app.modules.agentic.router import create_router
from app.modules.agentic.schemas import AnalyzeRequest
from app.modules.agentic.workflow import AgenticWorkflow
from app.modules.rag.schemas import AskResponse, Citation, PromptVersions
from app.modules.rag.context_builder import Evidence, EvidenceContext, EvidenceSource
from app.modules.retrieval.rerank_schemas import RerankResponse, RerankResult
from app.prompts.loader import PromptLoader


@pytest.fixture
def graph_stub(monkeypatch):
    graph = ModuleType("langgraph.graph")
    graph.START, graph.END = "__start__", "__end__"

    class StateGraph:
        def __init__(self, _state_type):
            self.nodes = {}
            self.edges = []
            self.conditionals = {}

        def add_node(self, name, function):
            self.nodes[name] = function

        def add_edge(self, source, target):
            self.edges.append((source, target))

        def add_conditional_edges(self, source, router, mapping):
            self.conditionals[source] = (router, mapping)

        def compile(self):
            assert (graph.START, "classify_intent") in self.edges
            assert "classify_intent" in self.conditionals
            assert "evidence_verification" in self.conditionals
            assert ("respond", graph.END) in self.edges
            return self

        def invoke(self, state, config):
            assert config == {"recursion_limit": 10}
            state.update(self.nodes["classify_intent"](state))
            router, mapping = self.conditionals["classify_intent"]
            selected = mapping[router(state)]
            state.update(self.nodes[selected](state))
            if selected != "clarify":
                state.update(self.nodes["evidence_aggregation"](state))
                state.update(self.nodes["evidence_verification"](state))
                router, mapping = self.conditionals["evidence_verification"]
                if mapping[router(state)] == "correct_answer":
                    state.update(self.nodes["correct_answer"](state))
                    state.update(self.nodes["verify_corrected"](state))
            state.update(self.nodes["respond"](state))
            return state

    graph.StateGraph = StateGraph
    package = ModuleType("langgraph")
    package.graph = graph
    monkeypatch.setitem(sys.modules, "langgraph", package)
    monkeypatch.setitem(sys.modules, "langgraph.graph", graph)


def result(name="code_analysis", *, repository_id, confidence="HIGH"):
    return AgentResult(
        answer="Observed: The cited source declares the behavior [E1]." if confidence != "INSUFFICIENT"
        else "Insufficient repository evidence to answer this question.",
        citations=[Citation(
            evidence_id="E1", file_path="src/OrderService.java",
            start_line=3, end_line=8,
        )] if confidence != "INSUFFICIENT" else [],
        confidence=confidence, limitations=[],
        prompt_versions=PromptVersions(name=name, system="1.1.0", user="1.1.0"),
        model_identifier="gemini-2.5-flash", evidence_count=1,
        evidence={"E1": AgentEvidence(
            key="source:1", repository_id=repository_id,
            file_path="src/OrderService.java", start_line=3, end_line=8,
            kind="source_code", content="class OrderService {}",
        )},
    )


def workflow_for(repository_id, *, classification=None, registered=True):
    code = SimpleNamespace(answer=MagicMock(return_value=result("code_analysis", repository_id=repository_id)))
    dependency = SimpleNamespace(answer=MagicMock(return_value=result("dependency_analysis", repository_id=repository_id)))
    architecture = SimpleNamespace(answer=MagicMock(return_value=result("architecture_analysis", repository_id=repository_id)))
    provider = SimpleNamespace(
        model_identifier="gemini-2.5-flash",
        generate_structured=MagicMock(return_value=classification),
    )
    classifier = IntentClassifier(PromptLoader(), provider)
    repositories = SimpleNamespace(
        registered_record=MagicMock(return_value=object() if registered else None),
    )
    clock = iter([1.0, 1.015])
    workflow = AgenticWorkflow(
        code, dependency, architecture, classifier, repositories,
        clock=lambda: next(clock),
    )
    return workflow, code, dependency, architecture, provider


@pytest.mark.parametrize(("question", "intent", "selected", "executed"), [
    ("Where is OrderService implemented?", "CODE_LOOKUP", "code_analysis", ["code_analysis"]),
    ("Explain OrderService.", "CODE_EXPLANATION", "code_analysis", ["code_analysis"]),
    ("What depends on AuthenticationService?", "DEPENDENCY_ANALYSIS", "dependency_analysis", ["dependency_analysis"]),
    ("What breaks if the authentication module changes?", "DEPENDENCY_ANALYSIS", "dependency_analysis", ["dependency_analysis"]),
    ("What changes are needed to replace RabbitMQ with Kafka?", "ARCHITECTURE_ANALYSIS",
     "architecture_analysis", ["code_analysis", "dependency_analysis", "architecture_analysis"]),
])
def test_supervisor_selects_only_needed_specialists(graph_stub, question, intent, selected, executed):
    repository_id = uuid4()
    workflow, code, dependency, architecture, provider = workflow_for(repository_id)
    response = workflow.analyze(AnalyzeRequest(repository_id=repository_id, question=question))
    metadata = response.workflow_metadata
    assert metadata.classified_intent == intent
    assert metadata.selected_workflow == selected
    assert metadata.agents_executed == executed
    assert metadata.evidence_count == 1
    assert metadata.execution_duration_ms == pytest.approx(15.0)
    assert metadata.prompt_versions.name == selected
    assert response.citations[0].file_path == "src/OrderService.java"
    assert code.answer.call_count == int(selected == "code_analysis")
    assert dependency.answer.call_count == int(selected == "dependency_analysis")
    assert architecture.answer.call_count == int(selected == "architecture_analysis")
    provider.generate_structured.assert_not_called()


def test_unknown_intent_requests_clarification_with_classifier_metadata(graph_stub):
    repository_id = uuid4()
    decision = IntentClassification(intent="UNKNOWN", routing_reason="Question is vague.")
    workflow, code, dependency, architecture, _ = workflow_for(
        repository_id, classification=decision,
    )
    response = workflow.analyze(AnalyzeRequest(
        repository_id=repository_id, question="Could you look at this?",
    ))
    assert response.workflow_metadata.answer_status == "CLARIFICATION_REQUIRED"
    assert response.workflow_metadata.agents_executed == []
    assert response.workflow_metadata.classifier_prompt_versions.system == "1.0.0"
    assert response.citations == []
    code.answer.assert_not_called()
    dependency.answer.assert_not_called()
    architecture.answer.assert_not_called()


def test_unknown_repository_stops_before_any_agent(graph_stub):
    repository_id = uuid4()
    workflow, code, dependency, architecture, provider = workflow_for(
        repository_id, registered=False,
    )
    with pytest.raises(ApplicationError) as error:
        workflow.analyze(AnalyzeRequest(
            repository_id=repository_id, question="What depends on AuthenticationService?",
        ))
    assert error.value.status_code == 404
    provider.generate_structured.assert_not_called()
    code.answer.assert_not_called()
    dependency.answer.assert_not_called()
    architecture.answer.assert_not_called()


def test_partial_verification_has_one_correction_and_no_fabricated_citation(graph_stub):
    repository_id = uuid4()
    workflow, code, _, _, _ = workflow_for(repository_id)
    original = code.answer.return_value
    code.answer.return_value = AgentResult(
        **{**original.__dict__, "answer": (
            "Observed: OrderService is declared [E1].\n"
            "Observed: It publishes Kafka events [E99]."
        )},
    )
    response = workflow.analyze(AnalyzeRequest(
        repository_id=repository_id, question="Explain OrderService.",
    ))
    assert response.verification_status == "PARTIALLY_VERIFIED"
    assert response.workflow_metadata.correction_attempts == 1
    assert response.answer == "Observed: OrderService is declared [E1]."
    assert [citation.evidence_id for citation in response.citations] == ["E1"]
    assert len(response.unsupported_claims) == 1
    assert response.claim_verifications[1].status == "INSUFFICIENT_EVIDENCE"


def test_all_unsupported_claims_fall_back_after_one_correction(graph_stub):
    repository_id = uuid4()
    workflow, code, _, _, _ = workflow_for(repository_id)
    original = code.answer.return_value
    code.answer.return_value = AgentResult(
        **{**original.__dict__, "answer": "Observed: Kafka is deployed [E99]."},
    )
    response = workflow.analyze(AnalyzeRequest(
        repository_id=repository_id, question="Explain OrderService.",
    ))
    assert response.verification_status == "INSUFFICIENT_EVIDENCE"
    assert response.workflow_metadata.correction_attempts == 1
    assert response.workflow_metadata.confidence == "INSUFFICIENT"
    assert response.citations == []
    assert response.answer == "Insufficient repository evidence to answer this question."


@pytest.mark.parametrize("agent", ["code", "dependency", "architecture"])
def test_agent_failures_are_controlled_and_not_hidden(graph_stub, agent):
    repository_id = uuid4()
    workflow, code, dependency, architecture, _ = workflow_for(repository_id)
    target = {"code": code, "dependency": dependency, "architecture": architecture}[agent]
    target.answer.side_effect = ApplicationError(503, "DATABASE_UNAVAILABLE", "Analysis is unavailable.")
    question = {
        "code": "Explain OrderService.",
        "dependency": "What depends on AuthenticationService?",
        "architecture": "What changes are needed to replace RabbitMQ with Kafka?",
    }[agent]
    with pytest.raises(ApplicationError) as error:
        workflow.analyze(AnalyzeRequest(repository_id=repository_id, question=question))
    assert error.value.code == "DATABASE_UNAVAILABLE"


def test_code_agent_reuses_rag_with_its_own_prompt(graph_stub):
    repository_id = uuid4()
    ranked = RerankResponse(
        repository_id=repository_id, reranker="metadata_heuristic_v1",
        results=[RerankResult(
            chunk_id="chunk-1", file_path="src/OrderService.java",
            original_rank=1, rerankerank=1, rerank_score=1.0,
            reranker_version="metadata_heuristic_v1",
        )],
    )
    retrieval = SimpleNamespace(search=MagicMock(return_value=ranked))
    response = AskResponse(
        repository_id=repository_id, status="ANSWERED", answer="Grounded.",
        citations=[Citation(evidence_id="E1", file_path="src/OrderService.java",
                            start_line=3, end_line=8)],
        confidence="HIGH", limitations=[], excluded_evidence_ids=[],
        prompt_versions=PromptVersions(name="code_analysis", system="1.0.0", user="1.0.0"),
        model_identifier="gemini-2.5-flash",
    )
    source = EvidenceSource(
        chunk_id="chunk-1", repository_id=repository_id,
        file_path="src/OrderService.java", qualified_name="demo.OrderService",
        start_line=3, end_line=8, start_byte=0, end_byte=20,
        content="class OrderService {}",
    )
    context = EvidenceContext(
        text="", evidence=(Evidence("E1", source),),
        excluded_evidence_ids=(), limitations=(),
    )
    rag = SimpleNamespace(answer=MagicMock(return_value=(response, context)))
    result_value = CodeAnalysisAgent(retrieval, rag).answer(repository_id, "Explain OrderService.", 3)
    assert result_value.evidence_count == 1
    assert retrieval.search.call_count == rag.answer.call_count == 1
    assert rag.answer.call_args.kwargs["reranked"] is ranked
    assert rag.answer.call_args.kwargs["prompt_name"] == "code_analysis"
    assert rag.answer.call_args.kwargs["defer_citation_validation"] is True


def test_openapi_and_mocked_api_examples(graph_stub):
    app = create_app()
    client = TestClient(app)
    path = "/api/v1/agents/analyze"
    specification = client.get("/openapi.json").json()
    operation = specification["paths"][path]["post"]
    assert {"200", "404", "409", "422", "429", "502", "503", "504"} <= set(operation["responses"])
    metadata = specification["components"]["schemas"]["WorkflowMetadata"]["properties"]
    assert {"classified_intent", "agents_executed", "prompt_versions",
            "evidence_count", "execution_duration_ms"} <= set(metadata)
    assert client.get("/docs").status_code == 200
    assert client.post(path, json={"repository_id": str(uuid4()), "question": " "}).status_code == 422
    assert client.post(path, json={"repository_id": str(uuid4()), "question": "Explain OrderService."}).status_code == 503

    repository_id = uuid4()
    injected = FastAPI()
    injected.add_exception_handler(ApplicationError, handle_application_error)
    injected.include_router(create_router(workflow_for(repository_id)[0]))
    response = TestClient(injected).post(path, json={
        "repository_id": str(repository_id), "question": "Explain OrderService.",
    })
    assert response.status_code == 200
    assert response.json()["workflow_metadata"]["agents_executed"] == ["code_analysis"]


@pytest.mark.parametrize(("question", "expected"), [
    ("Explain OrderService.", ["code_analysis"]),
    ("What depends on AuthenticationService?", ["dependency_analysis"]),
    ("What breaks if the authentication module changes?", ["dependency_analysis"]),
    ("What changes are needed to replace RabbitMQ with Kafka?",
     ["code_analysis", "dependency_analysis", "architecture_analysis"]),
])
def test_requested_examples_through_api(graph_stub, question, expected):
    repository_id = uuid4()
    injected = FastAPI()
    injected.include_router(create_router(workflow_for(repository_id)[0]))
    response = TestClient(injected).post("/api/v1/agents/analyze", json={
        "repository_id": str(repository_id), "question": question,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_metadata"]["agents_executed"] == expected
    assert body["workflow_metadata"]["prompt_versions"]["system"] == "1.1.0"
    assert body["verification_status"] == "CITATION_VERIFIED"


@pytest.mark.parametrize(("question", "branch", "answer", "expected"), [
    ("Explain OrderService.", "code", "Observed: OrderService is declared [E1].",
     "CITATION_VERIFIED"),
    ("What changes are needed to replace RabbitMQ with Kafka?", "architecture",
     "Observed: A source file exists [E1].\nProposed: Kafka is already deployed [E99].",
     "PARTIALLY_VERIFIED"),
    ("Explain OrderService.", "code", "Insufficient repository evidence to answer this question.",
     "INSUFFICIENT_EVIDENCE"),
    ("Explain OrderService.", "code", "Observed: OrderService is elsewhere [E1].",
     "INSUFFICIENT_EVIDENCE"),
])
def test_verification_scenarios_through_api(graph_stub, question, branch, answer, expected):
    repository_id = uuid4()
    workflow, code, _, architecture, _ = workflow_for(repository_id)
    target = code if branch == "code" else architecture
    original = target.answer.return_value
    changed = {**original.__dict__, "answer": answer}
    if answer.startswith("Insufficient"):
        changed.update(confidence="INSUFFICIENT", citations=[])
    elif "elsewhere" in answer:
        changed["citations"] = [Citation(
            evidence_id="E1", file_path="src/Wrong.java",
            start_line=3, end_line=8,
        )]
    target.answer.return_value = AgentResult(**changed)
    app = FastAPI()
    app.include_router(create_router(workflow))
    response = TestClient(app).post("/api/v1/agents/analyze", json={
        "repository_id": str(repository_id), "question": question,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["verification_status"] == expected
    if expected == "INSUFFICIENT_EVIDENCE":
        assert body["citations"] == []
    if expected == "PARTIALLY_VERIFIED":
        assert body["unsupported_claims"]
        assert "already deployed" not in body["answer"]
    if expected != "INSUFFICIENT_EVIDENCE":
        assert body["citations"]


def test_real_langgraph_runtime_when_installed():
    pytest.importorskip("langgraph.graph")
    repository_id = uuid4()
    response = workflow_for(repository_id)[0].analyze(AnalyzeRequest(
        repository_id=repository_id, question="Explain OrderService.",
    ))
    assert response.workflow_metadata.agents_executed == ["code_analysis"]
