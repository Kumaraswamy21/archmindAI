"""Health check route."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError, ErrorResponse
from app.schemas.health import HealthResponse, ReadinessResponse

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


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Check backend dependencies and database migration readiness",
    responses={503: {"model": ErrorResponse}},
)
def get_ready(request: Request) -> ReadinessResponse:
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        raise ApplicationError(
            503, "DATABASE_NOT_CONFIGURED", "Database is not configured."
        )
    if not getattr(request.app.state, "provider_configured", False):
        raise ApplicationError(
            503, "PROVIDER_NOT_CONFIGURED", "AI provider is not configured."
        )
    expected = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    ).get_current_head()
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                version = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                vector = connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                    )
                )
    except SQLAlchemyError as exc:
        raise ApplicationError(
            503, "DATABASE_UNAVAILABLE", "Database readiness check failed."
        ) from exc
    if version != expected or not vector:
        raise ApplicationError(
            503, "DATABASE_MIGRATION_REQUIRED", "Database migration is required."
        )
    return ReadinessResponse(status="ready", service="archmind")
