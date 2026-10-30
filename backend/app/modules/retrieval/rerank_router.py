"""HTTP endpoint for deterministic hybrid-result reranking."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.retrieval.rerank_schemas import RerankRequest, RerankResponse
from app.modules.retrieval.rerank_service import RerankSearchService


def create_router(service: RerankSearchService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/search", tags=["retrieval"])

    @router.post(
        "/rerank",
        response_model=RerankResponse,
        summary="Rerank hybrid code candidates with source metadata",
        description="Run both retrieval strategies, fuse their candidates, then apply deterministic metadata_heuristic_v1 scoring.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            409: {"model": ErrorResponse, "description": "Compatible semantic embeddings are unavailable."},
            429: {"model": ErrorResponse, "description": "Embedding provider rate limit."},
            502: {"model": ErrorResponse, "description": "Embedding provider failed or returned an invalid vector."},
            503: {"model": ErrorResponse, "description": "Search is not configured, database unavailable, or candidates changed."},
            504: {"model": ErrorResponse, "description": "Embedding provider timeout."},
        },
    )
    def rerank_search(request: RerankRequest) -> RerankResponse:
        if service is None:
            raise ApplicationError(503, "RERANK_NOT_CONFIGURED", "Reranking is not configured.")
        return service.search(request)

    return router
