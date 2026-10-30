"""Repository analysis API."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.agentic.schemas import AnalyzeRequest, AnalyzeResponse
from app.modules.agentic.workflow import AgenticWorkflow


def create_router(service: AgenticWorkflow | None) -> APIRouter:
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
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        if service is None:
            raise ApplicationError(503, "AGENTIC_NOT_CONFIGURED", "Repository analysis is not configured.")
        return service.analyze(request)

    return router
