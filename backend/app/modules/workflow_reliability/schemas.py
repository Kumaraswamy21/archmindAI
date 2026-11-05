"""Public workflow run contract."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


WorkflowStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
WorkflowType = Literal["chat", "ask", "agents.analyze", "index"]


class WorkflowRunResponse(BaseModel):
    workflow_id: UUID
    request_id: UUID
    repository_id: UUID
    workflow_type: WorkflowType
    status: WorkflowStatus
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    attempt_count: int = Field(ge=0)
    cancel_requested: bool = False
    state: dict[str, Any] = Field(default_factory=dict)
