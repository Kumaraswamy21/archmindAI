"""Intent-aware LangGraph supervisor for three read-only specialist agents."""

from time import perf_counter
from dataclasses import replace
from threading import Event
from typing import Callable
from uuid import UUID, uuid4

from app.errors import ApplicationError
from app.modules.agentic.architecture_analysis import ArchitectureAnalysisAgent
from app.modules.agentic.classifier import IntentClassifier
from app.modules.agentic.code_analysis import CodeAnalysisAgent
from app.modules.agentic.analysis_common import AgentEvidence
from app.modules.agentic.dependency_analysis import DependencyAnalysisAgent
from app.modules.agentic.schemas import AnalyzeRequest, AnalyzeResponse, WorkflowMetadata
from app.modules.agentic.evidence_verification import (
    EvidenceVerifier, INSUFFICIENT_ANSWER,
)
from app.modules.agentic.state import WorkflowState
from app.modules.repository_ingestion.service import RepositoryService


class WorkflowCancelled(Exception):
    """The streaming client disconnected or cancelled its request."""


class AgenticWorkflow:
    def __init__(
        self, code: CodeAnalysisAgent, dependency: DependencyAnalysisAgent,
        architecture: ArchitectureAnalysisAgent, classifier: IntentClassifier,
        repositories: RepositoryService, *, clock: Callable[[], float] = perf_counter,
        verifier: EvidenceVerifier | None = None,
    ) -> None:
        from langgraph.graph import END, START, StateGraph

        self._code = code
        self._dependency = dependency
        self._architecture = architecture
        self._classifier = classifier
        self._repositories = repositories
        self._clock = clock
        self._verifier = verifier or EvidenceVerifier()
        builder = StateGraph(WorkflowState)
        builder.add_node("classify_intent", self._classify_intent)
        builder.add_node("code_analysis", self._code_analysis)
        builder.add_node("dependency_analysis", self._dependency_analysis)
        builder.add_node("architecture_analysis", self._architecture_analysis)
        builder.add_node("clarify", self._clarify)
        builder.add_node("evidence_aggregation", self._aggregate_evidence)
        builder.add_node("evidence_verification", self._verify_evidence)
        builder.add_node("correct_answer", self._correct_answer)
        builder.add_node("verify_corrected", self._verify_corrected)
        builder.add_node("respond", self._respond)
        builder.add_edge(START, "classify_intent")
        builder.add_conditional_edges("classify_intent", self._route, {
            "code_analysis": "code_analysis",
            "dependency_analysis": "dependency_analysis",
            "architecture_analysis": "architecture_analysis",
            "clarification": "clarify",
        })
        for node in ("code_analysis", "dependency_analysis", "architecture_analysis"):
            builder.add_edge(node, "evidence_aggregation")
        builder.add_edge("clarify", "respond")
        builder.add_edge("evidence_aggregation", "evidence_verification")
        builder.add_conditional_edges(
            "evidence_verification", self._verification_route,
            {"correct": "correct_answer", "respond": "respond"},
        )
        builder.add_edge("correct_answer", "verify_corrected")
        builder.add_edge("verify_corrected", "respond")
        builder.add_edge("respond", END)
        self._graph = builder.compile()

    def _classify_intent(self, state: WorkflowState) -> dict[str, object]:
        if self._repositories.registered_record(state["repository_id"]) is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        decision = self._classifier.classify(state["question"])
        selected = {
            "CODE_LOOKUP": "code_analysis",
            "CODE_EXPLANATION": "code_analysis",
            "DEPENDENCY_ANALYSIS": "dependency_analysis",
            "ARCHITECTURE_ANALYSIS": "architecture_analysis",
            "UNKNOWN": "clarification",
        }[decision.classification.intent]
        return {
            "intent_decision": decision,
            "workflow_metadata": {
                "completed_nodes": ["classify_intent"],
                "retrieved_count": 0, "evidence_count": 0,
                "agents_executed": [],
                "classified_intent": decision.classification.intent,
                "selected_workflow": selected,
                "classifier_version": decision.classification.classifier_version,
                "routing_reason": decision.classification.routing_reason,
                "classifier_prompt_versions": decision.prompt_versions,
                "classifier_model_identifier": decision.model_identifier,
            },
        }

    @staticmethod
    def _route(state: WorkflowState) -> str:
        return str(state["workflow_metadata"]["selected_workflow"])

    def _code_analysis(self, state: WorkflowState) -> dict[str, object]:
        self._notify_start(state, "code_analysis")
        result = self._code.answer(state["repository_id"], state["question"], state["top_k"])
        return self._agent_update(state, result, "code_analysis", ["code_analysis"])

    def _dependency_analysis(self, state: WorkflowState) -> dict[str, object]:
        self._notify_start(state, "dependency_analysis")
        result = self._dependency.answer(state["repository_id"], state["question"])
        return self._agent_update(state, result, "dependency_analysis", ["dependency_analysis"])

    def _architecture_analysis(self, state: WorkflowState) -> dict[str, object]:
        self._notify_start(state, "architecture_analysis")
        result = self._architecture.answer(state["repository_id"], state["question"], state["top_k"])
        return self._agent_update(
            state, result, "architecture_analysis",
            ["code_analysis", "dependency_analysis", "architecture_analysis"],
        )

    @staticmethod
    def _notify_start(state: WorkflowState, agent: str) -> None:
        cancelled = state.get("cancel_event")
        if cancelled is not None and cancelled.is_set():
            raise WorkflowCancelled()
        sink = state.get("progress_sink")
        if sink is not None:
            sink("agent_started", {"agent": agent})

    @staticmethod
    def _agent_update(state, result, node, executed):
        return {
            "analysis": result,
            "workflow_metadata": {
                **state["workflow_metadata"],
                "completed_nodes": ["classify_intent", node],
                "agents_executed": executed,
                "evidence_count": result.evidence_count,
                "retrieved_count": result.evidence_count,
            },
        }

    @staticmethod
    def _aggregate_evidence(state: WorkflowState) -> dict[str, object]:
        analysis = state["analysis"]
        if analysis is None:
            raise ApplicationError(503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable.")
        return {
            "evidence": analysis.evidence or {},
            "workflow_metadata": {
                **state["workflow_metadata"],
                "completed_nodes": [
                    *state["workflow_metadata"]["completed_nodes"], "evidence_aggregation",
                ],
            },
        }

    def _verify_evidence(self, state: WorkflowState) -> dict[str, object]:
        analysis = state["analysis"]
        if analysis is None:
            raise ApplicationError(503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable.")
        report = self._verifier.verify(state["repository_id"], analysis)
        return {
            "verification": report,
            "unsupported_claims": report.unsupported_claims,
            "claim_verifications": report.claims,
            "workflow_metadata": {
                **state["workflow_metadata"],
                "verification_status": report.status,
                "completed_nodes": [
                    *state["workflow_metadata"]["completed_nodes"], "evidence_verification",
                ],
            },
        }

    @staticmethod
    def _verification_route(state: WorkflowState) -> str:
        report = state["verification"]
        return "correct" if report is not None and report.needs_correction else "respond"

    @staticmethod
    def _correct_answer(state: WorkflowState) -> dict[str, object]:
        report = state["verification"]
        if report is None:
            raise ApplicationError(503, "VERIFICATION_UNAVAILABLE", "Answer verification is unavailable.")
        corrected = "\n".join(report.supported_claims) if report.supported_claims else INSUFFICIENT_ANSWER
        citations = report.supported_citations if report.supported_claims else []
        return {
            "answer": corrected, "citations": citations, "correction_attempts": 1,
            "workflow_metadata": {
                **state["workflow_metadata"],
                "completed_nodes": [
                    *state["workflow_metadata"]["completed_nodes"], "correct_answer",
                ],
                "limitations": [
                    *(state["analysis"].limitations if state["analysis"] else []),
                    "Unsupported claims were removed after citation verification.",
                ],
            },
        }

    def _verify_corrected(self, state: WorkflowState) -> dict[str, object]:
        analysis = state["analysis"]
        if analysis is None or state["answer"] is None:
            raise ApplicationError(503, "VERIFICATION_UNAVAILABLE", "Answer verification is unavailable.")
        candidate = replace(
            analysis, answer=state["answer"], citations=state["citations"],
            confidence="INSUFFICIENT" if not state["citations"] else analysis.confidence,
        )
        report = self._verifier.verify(state["repository_id"], candidate)
        if report.needs_correction:
            # The graph has no retry edge. A failed correction becomes insufficient.
            answer, citations, status = INSUFFICIENT_ANSWER, [], "INSUFFICIENT_EVIDENCE"
        else:
            answer, citations = state["answer"], state["citations"]
            status = "PARTIALLY_VERIFIED" if citations else "INSUFFICIENT_EVIDENCE"
        return {
            "answer": answer, "citations": citations,
            "workflow_metadata": {
                **state["workflow_metadata"],
                "verification_status": status,
                "completed_nodes": [
                    *state["workflow_metadata"]["completed_nodes"], "verify_corrected",
                ],
            },
        }

    @staticmethod
    def _clarify(state: WorkflowState) -> dict[str, object]:
        return {
            "answer": "Please clarify which repository code or behavior you want to examine.",
            "citations": [],
            "workflow_metadata": {
                **state["workflow_metadata"],
                "completed_nodes": ["classify_intent", "clarify"],
                "answer_status": "CLARIFICATION_REQUIRED",
                "confidence": "INSUFFICIENT",
                "limitations": ["The question does not identify a supported analysis intent."],
                "excluded_evidence_ids": [],
                "verification_status": "NOT_APPLICABLE",
            },
        }

    @staticmethod
    def _respond(state: WorkflowState) -> dict[str, object]:
        analysis = state["analysis"]
        metadata = {**state["workflow_metadata"]}
        if analysis is not None:
            report = state["verification"]
            if report is None:
                raise ApplicationError(503, "VERIFICATION_UNAVAILABLE", "Answer verification is unavailable.")
            metadata.update({
                "answer_status": (
                    "INSUFFICIENT_EVIDENCE"
                    if metadata["verification_status"] == "INSUFFICIENT_EVIDENCE" else "ANSWERED"
                ),
                "confidence": (
                    "INSUFFICIENT" if metadata["verification_status"] == "INSUFFICIENT_EVIDENCE"
                    else analysis.confidence
                ),
                "limitations": metadata.get("limitations", analysis.limitations),
                "excluded_evidence_ids": [],
                "prompt_versions": analysis.prompt_versions,
                "model_identifier": analysis.model_identifier,
            })
            answer = state["answer"] if state["answer"] is not None else (
                analysis.answer if report.status == "INSUFFICIENT_EVIDENCE" else
                "\n".join(report.supported_claims)
            )
            citations = state["citations"] if state["answer"] is not None else report.supported_citations
        else:
            answer = state["answer"]
            citations = state["citations"]
        metadata["completed_nodes"] = [*metadata["completed_nodes"], "respond"]
        metadata["correction_attempts"] = state["correction_attempts"]
        return {"answer": answer, "citations": citations, "workflow_metadata": metadata}

    def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        response, _ = self.analyze_with_evidence(request)
        return response

    def analyze_with_evidence(
        self, request: AnalyzeRequest, *, request_id: UUID | None = None,
    ) -> tuple[AnalyzeResponse, dict[str, AgentEvidence]]:
        """Run the same graph and return its request-local evidence to trusted adapters."""
        started = self._clock()
        initial = self._initial_state(request, request_id=request_id)
        result: WorkflowState = self._graph.invoke(initial, config={"recursion_limit": 10})
        return self._response_from_state(result, started)

    def stream_with_evidence(
        self, request: AnalyzeRequest, *, request_id: UUID,
        emit: Callable[[str, dict[str, object]], None],
        cancel_event: Event,
    ) -> tuple[AnalyzeResponse, dict[str, AgentEvidence]]:
        """Run the same graph once and emit sanitized, actual node transitions."""
        started = self._clock()
        state = self._initial_state(
            request, request_id=request_id,
            progress_sink=emit, cancel_event=cancel_event,
        )
        for update in self._graph.stream(state, stream_mode="updates", config={"recursion_limit": 10}):
            if cancel_event.is_set():
                raise WorkflowCancelled()
            for node, values in update.items():
                state.update(values)
                if node == "classify_intent":
                    metadata = state["workflow_metadata"]
                    emit("intent_classified", {
                        "intent": metadata["classified_intent"],
                        "selected_workflow": metadata["selected_workflow"],
                    })
                elif node in {"code_analysis", "dependency_analysis", "architecture_analysis"}:
                    emit("agent_completed", {
                        "agent": node,
                        "evidence_count": state["workflow_metadata"]["evidence_count"],
                    })
                elif node in {"evidence_verification", "verify_corrected"}:
                    emit("evidence_verified", {
                        "verification_status": state["workflow_metadata"]["verification_status"],
                        "correction_pass": node == "verify_corrected",
                    })
        if cancel_event.is_set():
            raise WorkflowCancelled()
        return self._response_from_state(state, started)

    @staticmethod
    def _initial_state(
        request: AnalyzeRequest, *, request_id: UUID | None = None,
        progress_sink: Callable[[str, dict[str, object]], None] | None = None,
        cancel_event: Event | None = None,
    ) -> WorkflowState:
        return {
            "request_id": request_id or uuid4(), "repository_id": request.repository_id,
            "question": request.question, "top_k": request.top_k,
            "intent_decision": None, "retrieved_evidence": None,
            "analysis": None, "answer": None, "citations": [], "errors": [],
            "evidence": {}, "verification": None, "unsupported_claims": [],
            "claim_verifications": [], "correction_attempts": 0,
            "workflow_metadata": {"completed_nodes": [], "retrieved_count": 0},
            "progress_sink": progress_sink, "cancel_event": cancel_event,
        }

    def _response_from_state(
        self, result: WorkflowState, started: float,
    ) -> tuple[AnalyzeResponse, dict[str, AgentEvidence]]:
        if result["answer"] is None:
            raise ApplicationError(503, "ANALYSIS_UNAVAILABLE", "Repository analysis is unavailable.")
        metadata = {
            **result["workflow_metadata"],
            "execution_duration_ms": max(0.0, (self._clock() - started) * 1000.0),
        }
        response = AnalyzeResponse(
            request_id=result["request_id"], repository_id=result["repository_id"],
            answer=result["answer"], citations=result["citations"],
            verification_status=metadata["verification_status"],
            unsupported_claims=result["unsupported_claims"],
            claim_verifications=result["claim_verifications"],
            agents_executed=metadata["agents_executed"],
            prompt_versions=metadata.get("prompt_versions"),
            workflow_metadata=WorkflowMetadata.model_validate(metadata),
        )
        return response, result["evidence"]
