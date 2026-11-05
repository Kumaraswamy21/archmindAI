"""Public indexing job contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


Stage = Literal[
    "DISCOVERING", "PARSING", "CHUNKING", "PERSISTING", "EMBEDDING",
    "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION", "COMPLETED",
]
JobStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]


class IndexJobResponse(BaseModel):
    job_id: UUID
    repository_id: UUID
    status: JobStatus
    current_stage: Stage | None
    completed_stages: list[Stage]
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None


STAGES: tuple[str, ...] = (
    "DISCOVERING", "PARSING", "CHUNKING", "PERSISTING", "EMBEDDING",
    "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION",
)
