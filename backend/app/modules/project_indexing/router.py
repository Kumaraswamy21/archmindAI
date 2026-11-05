"""Nonblocking indexing job APIs."""

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError, ErrorResponse
from app.modules.project_indexing.persistence import IndexJobStore
from app.modules.project_indexing.schemas import IndexJobResponse
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.workflow_reliability.store import WorkflowStore


def create_router(
    jobs: IndexJobStore | None, repositories: RepositoryService,
    workflows: WorkflowStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/repositories", tags=["project-indexing"])

    def require_jobs() -> IndexJobStore:
        if jobs is None:
            raise ApplicationError(503, "DATABASE_NOT_CONFIGURED", "Indexing jobs are not configured.")
        return jobs

    @router.post("/{repository_id}/index", status_code=202,
                 response_model=IndexJobResponse,
                 responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                            503: {"model": ErrorResponse}},
                 summary="Queue a durable full-repository Java indexing job")
    def enqueue(repository_id: UUID) -> IndexJobResponse:
        store = require_jobs()
        if repositories.project(repository_id) is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        if repositories.registered_record(repository_id) is None:
            raise ApplicationError(503, "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable.")
        try:
            job = store.enqueue(repository_id)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Index job storage is unavailable.") from exc
        if workflows is not None:
            try:
                workflows.create(
                    repository_id=repository_id, request_id=job.job_id,
                    workflow_type="index", workflow_id=job.job_id,
                    state={"index_job_id": str(job.job_id), "index_stage": job.current_stage},
                )
            except ApplicationError:
                # The indexing job remains queued and recoverable; expose the storage failure.
                raise
        return job

    @router.post(
        "/{repository_id}/index/jobs/{job_id}/cancel",
        response_model=IndexJobResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                   503: {"model": ErrorResponse}},
        summary="Cancel a queued or running indexing job",
    )
    def cancel_job(repository_id: UUID, job_id: UUID) -> IndexJobResponse:
        try:
            result = require_jobs().request_cancel(repository_id, job_id)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Index job storage is unavailable.") from exc
        if workflows is not None:
            try:
                workflows.request_cancel(job_id, repository_id)
            except ApplicationError as exc:
                if exc.code != "WORKFLOW_NOT_CANCELLABLE":
                    raise
            workflows.sync_index(result)
        return result

    @router.get("/{repository_id}/index/jobs/latest",
                response_model=IndexJobResponse,
                responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
                summary="Get the latest persisted indexing job for a repository")
    def get_latest_job(repository_id: UUID) -> IndexJobResponse:
        try:
            result = require_jobs().latest(repository_id)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Index job storage is unavailable.") from exc
        if result is None:
            raise ApplicationError(404, "INDEX_JOB_NOT_FOUND", "Index job not found.")
        return result

    @router.get("/{repository_id}/index/jobs/{job_id}",
                response_model=IndexJobResponse,
                responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
                summary="Get a repository-scoped indexing job")
    def get_job(repository_id: UUID, job_id: UUID) -> IndexJobResponse:
        try:
            result = require_jobs().get(repository_id, job_id)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Index job storage is unavailable.") from exc
        if result is None:
            raise ApplicationError(404, "INDEX_JOB_NOT_FOUND", "Index job not found.")
        return result

    return router
