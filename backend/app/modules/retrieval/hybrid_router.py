"""Hybrid search HTTP endpoint."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.retrieval.hybrid_schemas import HybridSearchRequest, HybridSearchResponse
from app.modules.retrieval.hybrid_service import HybridSearchService


def create_router(service: HybridSearchService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/search", tags=["retrieval"])

    @router.post(
        "/hybrid",
        response_model=HybridSearchResponse,
        summary="Fuse semantic and keyword retrieval",
        description="Retrieve bounded candidates from both strategies and rank by reciprocal rank fusion. Both strategies must succeed.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            409: {"model": ErrorResponse, "description": "Compatible semantic embeddings are unavailable."},
            429: {"model": ErrorResponse, "description": "Embedding provider rate limit."},
            502: {"model": ErrorResponse, "description": "Embedding provider failed or returned an invalid vector."},
            503: {"model": ErrorResponse, "description": "Search is not configured, database unavailable, or results changed."},
            504: {"model": ErrorResponse, "description": "Embedding provider timeout."},
        },
    )
    def hybrid_search(request: HybridSearchRequest) -> HybridSearchResponse:
        if service is None:
            raise ApplicationError(503, "HYBRID_SEARCH_NOT_CONFIGURED", "Hybrid search is not configured.")
        return service.search(request)

    return router
