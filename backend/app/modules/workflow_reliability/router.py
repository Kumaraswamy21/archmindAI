"""Read and cancel persisted workflow runs."""

from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError, ErrorResponse
from app.modules.project_indexing.persistence import IndexJobStore
from app.modules.workflow_reliability import registry
from app.modules.workflow_reliability.schemas import WorkflowRunResponse
from app.modules.workflow_reliability.store import WorkflowStore


def create_router(store: WorkflowStore | None, jobs: IndexJobStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

    def require_store() -> WorkflowStore:
        if store is None:
            raise ApplicationError(503, "DATABASE_NOT_CONFIGURED", "Workflow storage is not configured.")
        return store

    @router.get(
        "/{workflow_id}", response_model=WorkflowRunResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        summary="Get a repository-scoped workflow run",
    )
    def get_workflow(
        workflow_id: UUID, repository_id: UUID = Query(...),
    ) -> WorkflowRunResponse:
        try:
            result = require_store().get(workflow_id, repository_id)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Workflow storage is unavailable.") from exc
        if result is None:
            raise ApplicationError(404, "WORKFLOW_NOT_FOUND", "Workflow not found.")
        if result.workflow_type == "index" and jobs is not None:
            job = jobs.get(repository_id, workflow_id)
            if job is not None:
                result = require_store().sync_index(job) or result
        return result

    @router.post(
        "/{workflow_id}/cancel", response_model=WorkflowRunResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                   503: {"model": ErrorResponse}},
        summary="Request cancellation of a running or pending workflow",
    )
    def cancel_workflow(
        workflow_id: UUID, repository_id: UUID = Query(...),
    ) -> WorkflowRunResponse:
        runs = require_store()
        existing = runs.get(workflow_id, repository_id)
        if existing is None:
            raise ApplicationError(404, "WORKFLOW_NOT_FOUND", "Workflow not found.")
        if existing.workflow_type == "index" and jobs is not None:
            job = jobs.request_cancel(repository_id, workflow_id)
            if existing.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                runs.request_cancel(workflow_id, repository_id)
            return runs.sync_index(job) or existing
        result = runs.request_cancel(workflow_id, repository_id)
        registry.signal(workflow_id)
        return result

    return router
