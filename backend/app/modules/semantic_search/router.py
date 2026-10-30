"""Semantic search HTTP endpoint."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.semantic_search.schemas import SemanticSearchRequest, SemanticSearchResponse
from app.modules.semantic_search.service import SemanticSearchService


def create_router(service: SemanticSearchService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/search", tags=["semantic-search"])

    @router.post(
        "/semantic",
        response_model=SemanticSearchResponse,
        summary="Search repository code by meaning",
        description="Embed a developer question and rank current, compatible repository chunks by exact pgvector cosine similarity.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            409: {"model": ErrorResponse, "description": "No compatible embeddings or incompatible configuration."},
            429: {"model": ErrorResponse, "description": "Embedding provider rate limit."},
            502: {"model": ErrorResponse, "description": "Embedding provider failure or invalid vector."},
            503: {"model": ErrorResponse, "description": "Search is not configured or database unavailable."},
            504: {"model": ErrorResponse, "description": "Embedding provider timeout."},
        },
    )
    def semantic_search(request: SemanticSearchRequest) -> SemanticSearchResponse:
        if service is None:
            raise ApplicationError(503, "SEARCH_NOT_CONFIGURED", "Semantic search is not configured.")
        return service.search(request)

    return router
