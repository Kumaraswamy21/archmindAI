"""Static dependency scan and repository-scoped edge listing."""

from uuid import UUID

from fastapi import APIRouter, Query

from app.errors import ApplicationError, ErrorResponse
from app.modules.dependency_intelligence.schemas import (
    DependencyListResponse, DependencyScanResponse, Direction, RelationshipType,
)
from app.modules.dependency_intelligence.service import DependencyIntelligenceService


def create_router(service: DependencyIntelligenceService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/repositories", tags=["dependency-intelligence"])

    @router.post(
        "/{repository_id}/dependencies/scan", response_model=DependencyScanResponse,
        summary="Scan a registered repository for static Java dependencies",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            413: {"model": ErrorResponse, "description": "Repository discovery or file size limit exceeded."},
            422: {"model": ErrorResponse, "description": "Java source has syntax errors or invalid encoding."},
            503: {"model": ErrorResponse, "description": "Database or registered repository unavailable."},
        },
    )
    def scan(repository_id: UUID) -> DependencyScanResponse:
        if service is None:
            raise ApplicationError(503, "DATABASE_NOT_CONFIGURED", "Cloud database is not configured.")
        return service.scan(repository_id)

    @router.get(
        "/{repository_id}/dependencies", response_model=DependencyListResponse,
        summary="List persisted static Java dependencies",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            503: {"model": ErrorResponse, "description": "Cloud database unavailable or not configured."},
        },
    )
    def list_dependencies(
        repository_id: UUID,
        symbol: str | None = Query(None, min_length=1, max_length=500),
        relationship_type: RelationshipType | None = Query(None),
        direction: Direction = Query("both"),
        limit: int = Query(100, ge=1, le=500),
    ) -> DependencyListResponse:
        if service is None:
            raise ApplicationError(503, "DATABASE_NOT_CONFIGURED", "Cloud database is not configured.")
        return service.list_relationships(
            repository_id, symbol=symbol, relationship_type=relationship_type,
            direction=direction, limit=limit,
        )

    return router
