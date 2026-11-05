"""Durable workflow run rows in managed PostgreSQL."""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.observability.redaction import question_fingerprint
from app.db.models import RepositoryRow, WorkflowRunRow
from app.errors import ApplicationError
from app.modules.workflow_reliability.schemas import WorkflowRunResponse, WorkflowType
from app.modules.project_indexing.schemas import IndexJobResponse


def _safe_state(state: dict[str, Any] | None, question: str | None = None) -> dict[str, Any]:
    allowed = {
        "current_node", "classified_intent", "selected_workflow", "agents_executed",
        "verification_status", "index_job_id", "index_stage", "answer_status",
        "question_length", "question_sha256", "attempt",
    }
    merged = {**(state or {}), **question_fingerprint(question)}
    clean: dict[str, Any] = {}
    for key, value in merged.items():
        if key not in allowed or value is None:
            continue
        if key == "agents_executed" and isinstance(value, list):
            clean[key] = [str(item) for item in value[:8]]
        elif isinstance(value, (str, int, float, bool)):
            clean[key] = value
    return clean


def _response(row: WorkflowRunRow) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        workflow_id=row.workflow_id, request_id=row.request_id,
        repository_id=row.repository_id, workflow_type=row.workflow_type,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        started_at=row.started_at, updated_at=row.updated_at, completed_at=row.completed_at,
        error_code=row.error_code, attempt_count=row.attempt_count,
        cancel_requested=row.cancel_requested, state=row.state or {},
    )


class WorkflowStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def create(
        self, *, repository_id: UUID, request_id: UUID, workflow_type: WorkflowType,
        workflow_id: UUID | None = None, question: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> WorkflowRunResponse:
        now = datetime.now(timezone.utc)
        try:
            with self._sessions.begin() as session:
                if session.get(RepositoryRow, repository_id) is None:
                    raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
                row = WorkflowRunRow(
                    workflow_id=workflow_id or uuid4(), request_id=request_id,
                    repository_id=repository_id, workflow_type=workflow_type,
                    status="PENDING", attempt_count=0, cancel_requested=False,
                    state=_safe_state(state, question), started_at=None,
                    updated_at=now, completed_at=None,
                )
                session.add(row)
                session.flush()
                return _response(row)
        except ApplicationError:
            raise
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def get(self, workflow_id: UUID, repository_id: UUID) -> WorkflowRunResponse | None:
        try:
            with self._sessions() as session:
                row = session.get(WorkflowRunRow, workflow_id)
                if row is None or row.repository_id != repository_id:
                    return None
                return _response(row)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def mark_running(self, workflow_id: UUID) -> None:
        self._update(workflow_id, status="RUNNING", started=True)

    def heartbeat(self, workflow_id: UUID, state: dict[str, Any] | None = None) -> None:
        self._update(workflow_id, state=state)

    def complete(self, workflow_id: UUID, state: dict[str, Any] | None = None) -> None:
        self._update(workflow_id, status="COMPLETED", state=state, completed=True)

    def fail(self, workflow_id: UUID, error_code: str, state: dict[str, Any] | None = None) -> None:
        self._update(workflow_id, status="FAILED", error_code=error_code, state=state, completed=True)

    def request_cancel(self, workflow_id: UUID, repository_id: UUID) -> WorkflowRunResponse:
        now = datetime.now(timezone.utc)
        try:
            with self._sessions.begin() as session:
                row = session.get(WorkflowRunRow, workflow_id, with_for_update=True)
                if row is None or row.repository_id != repository_id:
                    raise ApplicationError(404, "WORKFLOW_NOT_FOUND", "Workflow not found.")
                if row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    raise ApplicationError(409, "WORKFLOW_NOT_CANCELLABLE", "Workflow is no longer running.")
                row.cancel_requested = True
                row.updated_at = now
                if row.status == "PENDING":
                    row.status = "CANCELLED"
                    row.completed_at = now
                    row.error_code = "WORKFLOW_CANCELLED"
                return _response(row)
        except ApplicationError:
            raise
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def mark_cancelled(self, workflow_id: UUID) -> None:
        self._update(workflow_id, status="CANCELLED", error_code="WORKFLOW_CANCELLED", completed=True)

    def sync_index(self, job: IndexJobResponse) -> WorkflowRunResponse | None:
        """Mirror the authoritative, leased indexing job into its workflow record."""
        status = {"QUEUED": "PENDING"}.get(job.status, job.status)
        now = datetime.now(timezone.utc)
        try:
            with self._sessions.begin() as session:
                row = session.get(WorkflowRunRow, job.job_id, with_for_update=True)
                if row is None or row.repository_id != job.repository_id or row.workflow_type != "index":
                    return None
                row.status = status
                row.started_at = job.started_at
                row.completed_at = job.completed_at
                row.updated_at = now
                row.error_code = job.error_code
                row.state = _safe_state({"index_job_id": str(job.job_id),
                                         "index_stage": job.current_stage})
                return _response(row)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def record_attempt(self, workflow_id: UUID) -> int:
        try:
            with self._sessions.begin() as session:
                row = session.get(WorkflowRunRow, workflow_id, with_for_update=True)
                if row is None:
                    return 0
                if row.cancel_requested or row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    raise ApplicationError(409, "WORKFLOW_CANCELLED", "Workflow is no longer runnable.")
                row.attempt_count += 1
                row.updated_at = datetime.now(timezone.utc)
                if row.status == "PENDING":
                    row.status = "RUNNING"
                    row.started_at = row.started_at or row.updated_at
                return row.attempt_count
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def cancel_requested(self, workflow_id: UUID) -> bool:
        try:
            with self._sessions() as session:
                row = session.get(WorkflowRunRow, workflow_id)
                return bool(row is not None and row.cancel_requested)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def recover_stale(self, *, older_than_seconds: int = 180) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        try:
            with self._sessions.begin() as session:
                rows = session.scalars(select(WorkflowRunRow).where(
                    WorkflowRunRow.status.in_(("PENDING", "RUNNING")),
                    WorkflowRunRow.workflow_type != "index",
                    WorkflowRunRow.updated_at < cutoff,
                ).with_for_update(skip_locked=True)).all()
                for row in rows:
                    row.status = "CANCELLED" if row.cancel_requested else "FAILED"
                    row.error_code = "WORKFLOW_CANCELLED" if row.cancel_requested else "WORKFLOW_INTERRUPTED"
                    row.completed_at = datetime.now(timezone.utc)
                    row.updated_at = row.completed_at
                return len(rows)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc

    def _update(self, workflow_id: UUID, *, status: str | None = None, error_code: str | None = None,
                state: dict[str, Any] | None = None, started: bool = False, completed: bool = False) -> None:
        now = datetime.now(timezone.utc)
        try:
            with self._sessions.begin() as session:
                row = session.get(WorkflowRunRow, workflow_id, with_for_update=True)
                if row is None:
                    return
                if row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    return
                if row.cancel_requested and status in {"COMPLETED", "FAILED"}:
                    status = "CANCELLED"
                    error_code = "WORKFLOW_CANCELLED"
                    completed = True
                if status:
                    row.status = status
                if error_code:
                    row.error_code = error_code[:64]
                if state:
                    row.state = {**(row.state or {}), **_safe_state(state)}
                row.updated_at = now
                if started and row.started_at is None:
                    row.started_at = now
                if completed:
                    row.completed_at = now
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc
