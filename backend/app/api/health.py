"""Health check route."""

from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check service health",
    description="Return the current health status of the ArchMind API.",
    responses={200: {"description": "The service is healthy."}},
)
def get_health() -> HealthResponse:
    return HealthResponse(status="healthy", service="archmind")
