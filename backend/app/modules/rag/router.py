"""Grounded repository question-answering endpoint."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.rag.schemas import AskRequest, AskResponse
from app.modules.rag.service import RagService


def create_router(service: RagService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["rag"])

    @router.post(
        "/ask",
        response_model=AskResponse,
        summary="Answer a repository question using cited source evidence",
        description="Rerank retrieved code, assemble bounded evidence, and request a structured Gemini answer. Citations are checked against supplied evidence.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            409: {"model": ErrorResponse, "description": "Compatible retrieval embeddings are unavailable."},
            429: {"model": ErrorResponse, "description": "Gemini rate limit."},
            502: {"model": ErrorResponse, "description": "Provider failure, invalid output, or unsupported citation."},
            503: {"model": ErrorResponse, "description": "Database, retrieval, prompt, or model configuration unavailable."},
            504: {"model": ErrorResponse, "description": "Provider timeout."},
        },
    )
    def ask(request: AskRequest) -> AskResponse:
        if service is None:
            raise ApplicationError(503, "RAG_NOT_CONFIGURED", "Grounded answering is not configured.")
        return service.answer(request)

    return router
