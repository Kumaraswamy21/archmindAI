"""Retrieval evaluation HTTP endpoint."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.evaluation.agent_service import AgentEvaluationService
from app.modules.evaluation.schemas import (
    AgentEvaluationRequest,
    AgentEvaluationResponse,
    EvaluationRequest,
    EvaluationResponse,
)
from app.modules.evaluation.service import RetrievalEvaluationService


def create_router(
    service: RetrievalEvaluationService | None,
    agent_service: AgentEvaluationService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluation"])

    @router.post(
        "/retrieval",
        response_model=EvaluationResponse,
        summary="Measure four retrieval strategies on a pinned Java sample snapshot",
        description="Run the same versioned question set through semantic, keyword, hybrid, and reranked hybrid search. Return measured Recall@5, MRR, and end-to-end latency.",
        responses={
            404: {
                "model": ErrorResponse,
                "description": "Repository or dataset not found.",
            },
            409: {
                "model": ErrorResponse,
                "description": "Repository snapshot does not match or changed during evaluation; embeddings may be unavailable.",
            },
            413: {
                "model": ErrorResponse,
                "description": "Indexed repository exceeds the evaluation chunk limit.",
            },
            429: {
                "model": ErrorResponse,
                "description": "Embedding provider rate limit.",
            },
            502: {
                "model": ErrorResponse,
                "description": "Embedding provider failure or invalid vector.",
            },
            503: {
                "model": ErrorResponse,
                "description": "Evaluation, retrieval, or database unavailable.",
            },
            504: {"model": ErrorResponse, "description": "Embedding provider timeout."},
        },
    )
    def evaluate_retrieval(request: EvaluationRequest) -> EvaluationResponse:
        if service is None:
            raise ApplicationError(
                503,
                "EVALUATION_NOT_CONFIGURED",
                "Retrieval evaluation is not configured.",
            )
        return service.evaluate(request)

    @router.post(
        "/rag-agents",
        response_model=AgentEvaluationResponse,
        summary="Measure agent routing, grounding, and reliability on a pinned sample",
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def evaluate_rag_agents(request: AgentEvaluationRequest) -> AgentEvaluationResponse:
        if agent_service is None:
            raise ApplicationError(
                503, "EVALUATION_NOT_CONFIGURED", "Agent evaluation is not configured."
            )
        return agent_service.evaluate(request)

    return router
