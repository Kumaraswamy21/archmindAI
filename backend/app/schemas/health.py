"""Health check response model."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response returned by the health check."""

    status: Literal["healthy"]
    service: Literal["archmind"]
