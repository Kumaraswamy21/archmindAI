"""Repository analysis API."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request

from app.core.observability import request_trace
from app.errors import ApplicationError, ErrorResponse
from app.modules.agentic.schemas import AnalyzeRequest, AnalyzeResponse
from app.modules.agentic.workflow import AgenticWorkflow
from app.modules.workflow_reliability.runner import WorkflowRunner


def create_router(service: AgenticWorkflow | None, runner: WorkflowRunner | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/agents", tags=["agentic"])

    @router.post(
        "/analyze", response_model=AnalyzeResponse,
        summary="Analyze a repository question with a grounded workflow",
        description="Classify intent, then route to grounded code, static dependency, or architecture analysis. Unknown intent requests clarification.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            409: {"model": ErrorResponse, "description": "Compatible retrieval embeddings unavailable."},
            429: {"model": ErrorResponse, "description": "Gemini rate limit."},
            502: {"model": ErrorResponse, "description": "Provider failure or unsupported answer citation."},
            503: {"model": ErrorResponse, "description": "Retrieval, database, prompts, or model unavailable."},
            504: {"model": ErrorResponse, "description": "Provider timeout."},
        },
    )
    def analyze(body: AnalyzeRequest, request: Request) -> AnalyzeResponse:
        if service is None:
            raise ApplicationError(503, "AGENTIC_NOT_CONFIGURED", "Repository analysis is not configured.")
        correlation = getattr(request.state, "request_id", None)
        request_id = UUID(correlation) if correlation else uuid4()
        with request_trace(
            "agents.analyze", workflow_type="agents.analyze",
            repository_id=body.repository_id, request_id=request_id, question=body.question,
        ):
            if runner is None:
                return service.analyze(body, request_id=request_id)
            result, _ = runner.run(
                repository_id=body.repository_id, request_id=request_id,
                workflow_type="agents.analyze", question=body.question,
                workflow_id=request_id,
                operation=lambda _workflow_id: service.analyze(body, request_id=request_id),
            )
            return result

    return router
