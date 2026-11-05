"""Snapshot-pinned agent and grounding evaluation with sanitized results."""

import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from app.core.observability import get_tracer, request_trace
from app.errors import ApplicationError
from app.modules.agentic.classifier import CLASSIFIER_VERSION
from app.modules.agentic.schemas import AnalyzeRequest
from app.modules.agentic.workflow import AgenticWorkflow
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.evaluation.dataset_loader import (
    DatasetInvalid,
    DatasetLoader,
    DatasetNotFound,
)
from app.modules.evaluation.metrics import calculate_agent_metrics
from app.modules.evaluation.schemas import (
    AgentEvaluationRequest,
    AgentEvaluationResponse,
    AgentQuestionResult,
)
from app.modules.evaluation.snapshot import load_indexed_chunks, snapshot_id
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.workflow_reliability.runner import WorkflowRunner
from app.prompts.loader import PromptLoader
from app.prompts.registry import PromptError


class AgentEvaluationService:
    def __init__(
        self,
        repositories: RepositoryService,
        chunks: ChunkPersistence,
        workflow: AgenticWorkflow,
        loader: DatasetLoader,
        embedding_model: str,
        generation_model: str,
        reranker_version: str,
        *,
        runner: WorkflowRunner | None = None,
        clock=time.perf_counter,
    ) -> None:
        self._repositories = repositories
        self._chunks = chunks
        self._workflow = workflow
        self._loader = loader
        self._embedding_model = embedding_model
        self._generation_model = generation_model
        self._reranker_version = reranker_version
        self._runner = runner or WorkflowRunner(None)
        self._clock = clock

    def evaluate(self, request: AgentEvaluationRequest) -> AgentEvaluationResponse:
        try:
            dataset = self._loader.load_agent(request.dataset)
        except DatasetNotFound as exc:
            raise ApplicationError(
                404, "DATASET_NOT_FOUND", "Evaluation dataset not found."
            ) from exc
        except DatasetInvalid as exc:
            raise ApplicationError(
                503, "DATASET_INVALID", "Evaluation dataset is invalid."
            ) from exc
        chunks = load_indexed_chunks(
            self._repositories, self._chunks, request.repository_id
        )
        current_snapshot = snapshot_id(chunks)
        if current_snapshot != dataset.repository_snapshot_id:
            raise ApplicationError(
                409,
                "REPOSITORY_SNAPSHOT_MISMATCH",
                "Indexed repository does not match the evaluation snapshot.",
            )
        prompt_versions = self._prompt_versions()
        retrieval_settings = HybridSearchSettings()
        evaluation_id = uuid4()
        results: list[AgentQuestionResult] = []
        with request_trace(
            "evaluation.rag_agents",
            workflow_type="evaluation.rag_agents",
            repository_id=request.repository_id,
            request_id=evaluation_id,
            extra={
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.version,
                "repository_snapshot_id": current_snapshot,
                "embedding_model": self._embedding_model,
                "generation_model": self._generation_model,
                "reranker_version": self._reranker_version,
                "evaluation_run_id": str(evaluation_id),
            },
        ) as trace:
            for item in dataset.questions:
                started = self._clock()
                with get_tracer().span(
                    "evaluation.question",
                    metadata={
                        "evaluation_question_id": item.id,
                        "dataset_id": dataset.dataset_id,
                    },
                ) as question_trace:
                    correlation = uuid4()
                    try:
                        (answer, evidence), _ = self._runner.run(
                            repository_id=request.repository_id,
                            request_id=correlation,
                            workflow_type="agents.analyze",
                            question=item.question,
                            workflow_id=correlation,
                            operation=lambda _identity: self._workflow.analyze_with_evidence(
                                AnalyzeRequest(
                                    repository_id=request.repository_id,
                                    question=item.question,
                                    top_k=request.top_k,
                                ),
                                request_id=correlation,
                            ),
                        )
                        if answer.repository_id != request.repository_id:
                            raise ApplicationError(
                                503,
                                "RETRIEVAL_SCOPE_MISMATCH",
                                "Analysis crossed repository scope.",
                            )
                        valid_citations = sum(
                            self._valid_citation(
                                request.repository_id, citation, evidence
                            )
                            for citation in answer.citations
                        )
                        results.append(
                            AgentQuestionResult(
                                question_id=item.id,
                                category=item.category,
                                status="COMPLETED",
                                classified_intent=answer.workflow_metadata.classified_intent,
                                selected_workflow=answer.workflow_metadata.selected_workflow,
                                answer_status=answer.workflow_metadata.answer_status,
                                verification_status=answer.verification_status,
                                citation_count=len(answer.citations),
                                valid_citation_count=valid_citations,
                                supported_claim_count=sum(
                                    claim.status == "CITATION_VERIFIED"
                                    for claim in answer.claim_verifications
                                ),
                                unsupported_claim_count=len(answer.unsupported_claims),
                                latency_ms=max(0.0, (self._clock() - started) * 1000),
                            )
                        )
                        question_trace.set(
                            metadata={
                                "verification_status": answer.verification_status,
                                "citation_count": len(answer.citations),
                                "unsupported_claim_count": len(
                                    answer.unsupported_claims
                                ),
                            },
                            output={"status": "COMPLETED"},
                        )
                    except ApplicationError as exc:
                        results.append(self._failure(item, started, exc.code))
                        question_trace.record_error(exc)
                    except Exception:
                        results.append(self._failure(item, started, "WORKFLOW_FAILED"))
                        question_trace.set(
                            metadata={"error_code": "WORKFLOW_FAILED"},
                            output={"status": "FAILED"},
                        )
            if (
                snapshot_id(
                    load_indexed_chunks(
                        self._repositories, self._chunks, request.repository_id
                    )
                )
                != current_snapshot
            ):
                raise ApplicationError(
                    409,
                    "REPOSITORY_SNAPSHOT_CHANGED",
                    "Indexed repository changed during evaluation.",
                )
            metrics = calculate_agent_metrics(dataset.questions, results)
            trace.set(
                output={
                    "query_count": len(results),
                    "workflow_completion_rate": metrics.workflow_completion_rate,
                    "workflow_failure_rate": metrics.workflow_failure_rate,
                }
            )
        return AgentEvaluationResponse(
            repository_id=request.repository_id,
            dataset=dataset.dataset_id,
            dataset_version=dataset.version,
            repository_snapshot_id=current_snapshot,
            evaluated_at=datetime.now(timezone.utc),
            query_count=len(results),
            embedding_model_identifier=self._embedding_model,
            generation_model_identifier=self._generation_model,
            retrieval_configuration={
                "top_k": request.top_k,
                "rrf_constant": retrieval_settings.hybrid_rrf_constant,
                "candidate_pool": retrieval_settings.hybrid_candidate_pool,
            },
            reranker_version=self._reranker_version,
            classifier_version=CLASSIFIER_VERSION,
            prompt_versions=prompt_versions,
            metrics=metrics,
            questions=results,
        )

    def _failure(self, item, started: float, code: str) -> AgentQuestionResult:
        return AgentQuestionResult(
            question_id=item.id,
            category=item.category,
            status="FAILED",
            error_code=code,
            citation_count=0,
            valid_citation_count=0,
            supported_claim_count=0,
            unsupported_claim_count=0,
            latency_ms=max(0.0, (self._clock() - started) * 1000),
        )

    @staticmethod
    def _valid_citation(repository_id, citation, evidence: dict) -> bool:
        item = evidence.get(citation.evidence_id)
        return bool(
            item is not None
            and item.repository_id == repository_id
            and citation.file_path == item.file_path
            and citation.start_line == item.start_line
            and citation.end_line == item.end_line
            and item.start_line >= 1
            and item.end_line >= item.start_line
        )

    @staticmethod
    def _prompt_versions() -> dict[str, dict[str, str]]:
        loader = PromptLoader()
        versions: dict[str, dict[str, str]] = {}
        try:
            for name in (
                "rag_answer",
                "query_classifier",
                "code_analysis",
                "dependency_analysis",
                "architecture_analysis",
            ):
                prefix = "RAG" if name == "rag_answer" else name.upper()
                loaded = loader.load(
                    name,
                    system_version=os.environ.get(f"{prefix}_SYSTEM_PROMPT_VERSION"),
                    user_version=os.environ.get(f"{prefix}_USER_PROMPT_VERSION"),
                )
                versions[name] = {
                    "system": loaded.system_version,
                    "user": loaded.user_version,
                }
        except PromptError as exc:
            raise ApplicationError(
                503, "PROMPT_CONFIGURATION_ERROR", "Evaluation prompts are unavailable."
            ) from exc
        return versions
