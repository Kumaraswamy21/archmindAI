"""Embedding generation API without exposing vectors or provider details."""

from uuid import UUID

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.vector_store.schemas import EmbeddingGenerationResponse, IndexStatusResponse
from app.modules.vector_store.service import EmbeddingService
from app.modules.vector_store.status import IndexStatusService


def create_router(
    service: EmbeddingService | None,
    status_service: IndexStatusService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/repositories", tags=["vector-store"])

    @router.post(
        "/{repository_id}/embeddings",
        response_model=EmbeddingGenerationResponse,
        summary="Generate embeddings for persisted code chunks",
        description="Batch Gemini embeddings for persisted chunks; return counts and model only. Vectors are stored in cloud PostgreSQL and never returned.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            429: {"model": ErrorResponse, "description": "Provider rate limit exhausted."},
            502: {"model": ErrorResponse, "description": "Provider failed or returned invalid dimensions."},
            503: {"model": ErrorResponse, "description": "Cloud database or Gemini credentials are unavailable."},
            504: {"model": ErrorResponse, "description": "Provider timeout after bounded retries."},
        },
    )
    def generate_repository_embeddings(
        repository_id: UUID,
    ) -> EmbeddingGenerationResponse:
        if service is None:
            raise ApplicationError(
                503, "EMBEDDING_NOT_CONFIGURED",
                "Embedding generation is not configured.",
            )
        return service.generate(repository_id)

    @router.get(
        "/{repository_id}/index/status",
        response_model=IndexStatusResponse,
        summary="Inspect persisted vector coverage",
        description="Count current vectors for the configured embedding model, dimension, and input format. No vectors are returned.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            503: {"model": ErrorResponse, "description": "Cloud database is not configured or is unavailable."},
        },
    )
    def get_repository_index_status(repository_id: UUID) -> IndexStatusResponse:
        if status_service is None:
            raise ApplicationError(
                503, "DATABASE_NOT_CONFIGURED", "Cloud database is not configured."
            )
        return status_service.get_status(repository_id)

    return router
