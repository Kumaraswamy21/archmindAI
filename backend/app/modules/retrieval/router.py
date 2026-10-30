"""Keyword and exact-symbol search endpoint."""

from fastapi import APIRouter

from app.errors import ApplicationError, ErrorResponse
from app.modules.retrieval.schemas import KeywordSearchRequest, KeywordSearchResponse
from app.modules.retrieval.service import KeywordSearchService


def create_router(service: KeywordSearchService | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/search", tags=["retrieval"])

    @router.post(
        "/keyword",
        response_model=KeywordSearchResponse,
        summary="Find Java chunks by exact symbol or full-text keywords",
        description="Exact case-sensitive symbol names rank first, followed by enclosing class names and PostgreSQL full-text matches. Results are restricted to one repository.",
        responses={
            404: {"model": ErrorResponse, "description": "Repository not found."},
            503: {"model": ErrorResponse, "description": "Cloud database is not configured or is unavailable."},
        },
    )
    def keyword_search(request: KeywordSearchRequest) -> KeywordSearchResponse:
        if service is None:
            raise ApplicationError(503, "DATABASE_NOT_CONFIGURED", "Cloud database is not configured.")
        return service.search(request)

    return router
