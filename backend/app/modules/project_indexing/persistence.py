"""PostgreSQL queue, staging, and atomic active-snapshot promotion."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ChunkEmbeddingRow, CodeChunkRow, DependencyRelationshipRow, IndexJobRow,
    IndexStagedChunkRow, IndexStagedEdgeRow, RepositoryRow,
)
from app.errors import ApplicationError
from app.modules.project_indexing.schemas import IndexJobResponse, STAGES
from app.modules.vector_store.input_builder import EMBEDDING_INPUT_VERSION


LEASE_SECONDS = 180


RESUMABLE_STAGES = frozenset({"EMBEDDING", "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION"})


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    repository_id: UUID
    lease_token: UUID
    current_stage: str = "DISCOVERING"
    completed_stages: tuple[str, ...] = ()
    source_snapshot_id: str | None = None


def _response(row: IndexJobRow) -> IndexJobResponse:
    return IndexJobResponse(
        job_id=row.job_id, repository_id=row.repository_id, status=row.status,
        current_stage=row.current_stage, completed_stages=row.completed_stages,
        started_at=row.started_at, completed_at=row.completed_at,
        error_code=row.error_code, error_message=row.error_message,
    )


class IndexJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def enqueue(self, repository_id: UUID) -> IndexJobResponse:
        try:
            with self._sessions.begin() as session:
                if session.get(RepositoryRow, repository_id, with_for_update=True) is None:
                    raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
                row = IndexJobRow(
                    job_id=uuid4(), repository_id=repository_id, status="QUEUED",
                    current_stage="DISCOVERING", completed_stages=[],
                )
                session.add(row)
                session.flush()
                result = _response(row)
            return result
        except IntegrityError:
            raise ApplicationError(409, "INDEX_JOB_ACTIVE", "An indexing job is already active for this repository.") from None

    def get(self, repository_id: UUID, job_id: UUID) -> IndexJobResponse | None:
        with self._sessions() as session:
            row = session.get(IndexJobRow, job_id)
            if row is None or row.repository_id != repository_id:
                return None
            return _response(row)

    def latest(self, repository_id: UUID) -> IndexJobResponse | None:
        with self._sessions() as session:
            row = session.scalar(select(IndexJobRow).where(
                IndexJobRow.repository_id == repository_id,
            ).order_by(IndexJobRow.created_at.desc(), IndexJobRow.job_id.desc()).limit(1))
            return _response(row) if row is not None else None

    def claim(self) -> ClaimedJob | None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            row = session.scalar(
                select(IndexJobRow).where(
                    (IndexJobRow.status == "QUEUED") |
                    ((IndexJobRow.status == "RUNNING") & (IndexJobRow.lease_until < now))
                ).order_by(IndexJobRow.created_at, IndexJobRow.job_id)
                .with_for_update(skip_locked=True).limit(1)
            )
            if row is None:
                return None
            token = uuid4()
            staged = session.scalar(select(func.count()).select_from(IndexStagedChunkRow).where(
                IndexStagedChunkRow.job_id == row.job_id,
            )) or 0
            resumable = (
                row.status == "RUNNING"
                and row.current_stage in RESUMABLE_STAGES
                and bool(row.source_snapshot_id)
                and int(staged) > 0
            )
            if not resumable:
                session.execute(delete(IndexStagedEdgeRow).where(IndexStagedEdgeRow.job_id == row.job_id))
                session.execute(delete(IndexStagedChunkRow).where(IndexStagedChunkRow.job_id == row.job_id))
                row.current_stage = "DISCOVERING"
                row.completed_stages = []
                row.source_snapshot_id = None
                row.embedding_model = None
                row.embedding_dimension = None
            row.status = "RUNNING"
            row.started_at = row.started_at or now
            row.completed_at = None
            if row.cancel_requested_at is not None:
                row.status = "CANCELLED"
                row.error_code = "INDEX_JOB_CANCELLED"
                row.error_message = "Indexing cancelled at user request."
                row.completed_at = now
                row.lease_until = row.lease_token = None
                session.execute(delete(IndexStagedEdgeRow).where(IndexStagedEdgeRow.job_id == row.job_id))
                session.execute(delete(IndexStagedChunkRow).where(IndexStagedChunkRow.job_id == row.job_id))
                return None
            row.error_code = row.error_message = None
            row.lease_token = token
            row.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            return ClaimedJob(
                row.job_id, row.repository_id, token,
                current_stage=row.current_stage or "DISCOVERING",
                completed_stages=tuple(row.completed_stages or []),
                source_snapshot_id=row.source_snapshot_id,
            )

    def heartbeat(self, job: ClaimedJob) -> None:
        with self._sessions.begin() as session:
            row = self._owned(session, job)
            row.lease_until = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)

    def advance(self, job: ClaimedJob, completed: str, next_stage: str,
                snapshot_id: str | None = None) -> None:
        with self._sessions.begin() as session:
            row = self._owned(session, job)
            if row.current_stage != completed or completed not in STAGES:
                raise RuntimeError("Invalid indexing stage transition.")
            row.completed_stages = [*row.completed_stages, completed]
            row.current_stage = next_stage
            if snapshot_id is not None:
                row.source_snapshot_id = snapshot_id
            row.lease_until = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)

    def stage_chunks(self, job: ClaimedJob, payloads: list[dict]) -> None:
        if not payloads:
            return
        with self._sessions.begin() as session:
            self._owned(session, job)
            session.add_all(IndexStagedChunkRow(job_id=job.job_id,
                                                chunk_id=payload["chunk_id"], payload=payload)
                            for payload in payloads)

    def staged_chunks(self, job: ClaimedJob) -> list[tuple[str, dict, list[float] | None]]:
        with self._sessions() as session:
            rows = session.scalars(select(IndexStagedChunkRow).where(
                IndexStagedChunkRow.job_id == job.job_id
            ).order_by(IndexStagedChunkRow.chunk_id)).all()
            return [(row.chunk_id, row.payload, row.embedding) for row in rows]

    def set_embedding(self, job: ClaimedJob, chunk_id: str,
                      input_hash: str, vector: list[float]) -> None:
        with self._sessions.begin() as session:
            self._owned(session, job)
            session.execute(update(IndexStagedChunkRow).where(
                IndexStagedChunkRow.job_id == job.job_id,
                IndexStagedChunkRow.chunk_id == chunk_id,
            ).values(input_hash=input_hash, embedding=vector))

    def validate_vectors(self, job: ClaimedJob, dimension: int) -> int:
        with self._sessions() as session:
            total, ready = session.execute(select(
                func.count(IndexStagedChunkRow.chunk_id),
                func.count(IndexStagedChunkRow.embedding),
            ).where(IndexStagedChunkRow.job_id == job.job_id)).one()
            if total != ready:
                raise ApplicationError(502, "VECTOR_INDEX_INCOMPLETE", "Not every chunk has a stored vector.")
            bad = session.scalar(select(func.count()).select_from(IndexStagedChunkRow).where(
                IndexStagedChunkRow.job_id == job.job_id,
                IndexStagedChunkRow.embedding.is_not(None),
                func.vector_dims(IndexStagedChunkRow.embedding) != dimension,
            ))
            if bad:
                raise ApplicationError(502, "VECTOR_DIMENSION_INVALID", "A staged vector has invalid dimensions.")
            return int(total)

    def stage_edges(self, job: ClaimedJob, payloads: list[dict]) -> None:
        with self._sessions.begin() as session:
            self._owned(session, job)
            session.add_all(IndexStagedEdgeRow(job_id=job.job_id,
                                               relationship_id=payload["relationship_id"], payload=payload)
                            for payload in payloads)

    def promote(self, job: ClaimedJob, snapshot_id: str, model: str, dimension: int) -> None:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            row = self._owned(session, job)
            if row.current_stage != "DEPENDENCY_EXTRACTION" or row.source_snapshot_id != snapshot_id:
                raise RuntimeError("Invalid snapshot promotion state.")
            chunks = session.scalars(select(IndexStagedChunkRow).where(
                IndexStagedChunkRow.job_id == job.job_id
            ).order_by(IndexStagedChunkRow.chunk_id)).all()
            if any(item.embedding is None or item.input_hash is None for item in chunks):
                raise ApplicationError(502, "VECTOR_INDEX_INCOMPLETE", "Not every chunk has a stored vector.")
            edges = session.scalars(select(IndexStagedEdgeRow).where(
                IndexStagedEdgeRow.job_id == job.job_id
            ).order_by(IndexStagedEdgeRow.relationship_id)).all()
            session.execute(delete(DependencyRelationshipRow).where(
                DependencyRelationshipRow.repository_id == job.repository_id
            ))
            session.execute(delete(CodeChunkRow).where(CodeChunkRow.repository_id == job.repository_id))
            for item in chunks:
                payload = dict(item.payload)
                payload["repository_id"] = job.repository_id
                payload["source_snapshot_id"] = snapshot_id
                payload["content_hash"] = hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
                session.execute(insert(CodeChunkRow).values(**payload))
                session.execute(insert(ChunkEmbeddingRow).values(
                    chunk_id=item.chunk_id, model_identifier=model,
                    output_dimension=dimension, input_version=EMBEDDING_INPUT_VERSION,
                    input_hash=item.input_hash, status="COMPLETED",
                    embedding=item.embedding, generated_at=now,
                ))
            for item in edges:
                payload = dict(item.payload)
                payload["repository_id"] = job.repository_id
                payload["source_snapshot_id"] = snapshot_id
                session.execute(insert(DependencyRelationshipRow).values(**payload))
            project = session.get(RepositoryRow, job.repository_id)
            if project is None:
                raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
            project.active_snapshot_id = snapshot_id
            row.status = "COMPLETED"
            row.current_stage = "COMPLETED"
            row.completed_stages = [*row.completed_stages, "DEPENDENCY_EXTRACTION"]
            row.completed_at = now
            row.lease_until = row.lease_token = None
            row.embedding_model = model
            row.embedding_dimension = dimension
            session.execute(delete(IndexStagedEdgeRow).where(IndexStagedEdgeRow.job_id == job.job_id))
            session.execute(delete(IndexStagedChunkRow).where(IndexStagedChunkRow.job_id == job.job_id))

    def fail(self, job: ClaimedJob, code: str, message: str) -> None:
        with self._sessions.begin() as session:
            row = self._owned(session, job, allow_cancel=True)
            row.status = "CANCELLED" if code == "INDEX_JOB_CANCELLED" else "FAILED"
            row.error_code = code[:64]
            row.error_message = message[:240]
            row.completed_at = datetime.now(timezone.utc)
            row.lease_until = row.lease_token = None
            session.execute(delete(IndexStagedEdgeRow).where(IndexStagedEdgeRow.job_id == job.job_id))
            session.execute(delete(IndexStagedChunkRow).where(IndexStagedChunkRow.job_id == job.job_id))

    def request_cancel(self, repository_id: UUID, job_id: UUID) -> IndexJobResponse:
        now = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            row = session.get(IndexJobRow, job_id, with_for_update=True)
            if row is None or row.repository_id != repository_id:
                raise ApplicationError(404, "INDEX_JOB_NOT_FOUND", "Index job not found.")
            if row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise ApplicationError(409, "WORKFLOW_NOT_CANCELLABLE", "Indexing is no longer running.")
            row.cancel_requested_at = now
            if row.status == "QUEUED":
                row.status = "CANCELLED"
                row.error_code = "INDEX_JOB_CANCELLED"
                row.error_message = "Indexing cancelled at user request."
                row.completed_at = now
            return _response(row)

    def is_cancel_requested(self, job: ClaimedJob) -> bool:
        with self._sessions() as session:
            row = session.get(IndexJobRow, job.job_id)
            return bool(row is not None and row.cancel_requested_at is not None)

    @staticmethod
    def _owned(session: Session, job: ClaimedJob, *, allow_cancel: bool = False) -> IndexJobRow:
        row = session.get(IndexJobRow, job.job_id, with_for_update=True)
        if row is None or row.lease_token != job.lease_token or row.status != "RUNNING":
            raise RuntimeError("Index job lease was lost.")
        if row.cancel_requested_at is not None and not allow_cancel:
            raise ApplicationError(409, "INDEX_JOB_CANCELLED", "Indexing cancelled at user request.")
        return row
