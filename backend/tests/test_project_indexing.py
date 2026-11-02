"""Index orchestration, failure, and public job contract without local PostgreSQL."""

from collections import deque
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import ApplicationError, handle_application_error
from app.modules.project_indexing.persistence import ClaimedJob
from app.modules.project_indexing.router import create_router
from app.modules.project_indexing.schemas import IndexJobResponse, STAGES
from app.modules.project_indexing.worker import IndexWorker
from app.modules.repository_ingestion.registry import RepositoryRegistry
from app.modules.repository_ingestion.schemas import RepositoryRegistrationRequest
from app.modules.repository_ingestion.security import RepositoryConfiguration
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.vector_store.config import EmbeddingSettings
from app.modules.vector_store.embeddings.base import EmbeddingProviderError


class FakeJobs:
    def __init__(self):
        self.queue = deque()
        self.rows = {}
        self.chunks = {}
        self.edges = {}
        self.active = {}

    def enqueue(self, repository_id):
        if any(row.repository_id == repository_id and row.status in ("QUEUED", "RUNNING")
               for row in self.rows.values()):
            raise ApplicationError(409, "INDEX_JOB_ACTIVE", "An indexing job is already active for this repository.")
        job_id = uuid4()
        row = IndexJobResponse(job_id=job_id, repository_id=repository_id, status="QUEUED",
                               current_stage="DISCOVERING", completed_stages=[],
                               started_at=None, completed_at=None, error_code=None, error_message=None)
        self.rows[job_id] = row
        self.queue.append(job_id)
        return row

    def get(self, repository_id, job_id):
        row = self.rows.get(job_id)
        return row if row and row.repository_id == repository_id else None

    def latest(self, repository_id):
        rows = [row for row in self.rows.values() if row.repository_id == repository_id]
        return rows[-1] if rows else None

    def claim(self):
        if not self.queue:
            return None
        job_id = self.queue.popleft()
        row = self.rows[job_id]
        self.rows[job_id] = row.model_copy(update={"status": "RUNNING"})
        self.chunks[job_id] = {}
        self.edges[job_id] = {}
        return ClaimedJob(job_id, row.repository_id, uuid4())

    def heartbeat(self, job):
        pass

    def advance(self, job, completed, next_stage, snapshot_id=None):
        row = self.rows[job.job_id]
        assert row.current_stage == completed
        self.rows[job.job_id] = row.model_copy(update={
            "current_stage": next_stage,
            "completed_stages": [*row.completed_stages, completed],
        })
        if snapshot_id:
            self.snapshot = snapshot_id

    def stage_chunks(self, job, payloads):
        self.chunks[job.job_id].update({p["chunk_id"]: [p, None] for p in payloads})

    def staged_chunks(self, job):
        return [(key, value[0], value[1]) for key, value in sorted(self.chunks[job.job_id].items())]

    def set_embedding(self, job, chunk_id, input_hash, vector):
        self.chunks[job.job_id][chunk_id][1] = vector

    def validate_vectors(self, job, dimension):
        assert all(len(value[1]) == dimension for value in self.chunks[job.job_id].values())
        return len(self.chunks[job.job_id])

    def stage_edges(self, job, payloads):
        self.edges[job.job_id].update({p["relationship_id"]: p for p in payloads})

    def promote(self, job, snapshot_id, model, dimension):
        assert all(value[1] is not None for value in self.chunks[job.job_id].values())
        self.active[job.repository_id] = (snapshot_id, tuple(sorted(self.chunks[job.job_id])),
                                          tuple(sorted(self.edges[job.job_id])))
        row = self.rows[job.job_id]
        self.rows[job.job_id] = row.model_copy(update={
            "status": "COMPLETED", "current_stage": "COMPLETED",
            "completed_stages": [*row.completed_stages, "DEPENDENCY_EXTRACTION"],
        })

    def fail(self, job, code, message):
        row = self.rows[job.job_id]
        self.rows[job.job_id] = row.model_copy(update={
            "status": "FAILED", "error_code": code, "error_message": message,
        })


class FakeProvider:
    model_identifier = "fake-embedding"
    output_dimension = 128

    def __init__(self):
        self.fail = False

    def embed_documents(self, texts):
        if self.fail:
            raise EmbeddingProviderError("private provider text")
        return [[0.1] * 128 for _ in texts]


def _setup(tmp_path: Path):
    root = tmp_path / "approved"
    root.mkdir()
    source = root / "Service.java"
    original = "public class Service { public void run() {} }\n"
    source.write_text(original)
    config = RepositoryConfiguration({"sample": root}, (root,), 100, 100000)
    repositories = RepositoryService(config, RepositoryRegistry())
    repository_id = repositories.register(RepositoryRegistrationRequest(
        name="Sample", source_alias="sample",
    )).repository_id
    jobs = FakeJobs()
    provider = FakeProvider()
    settings = EmbeddingSettings(gemini_api_key="fake", embedding_model="fake-embedding",
                                 embedding_dimension=128, embedding_batch_size=4)
    worker = IndexWorker(jobs, repositories, provider, settings)
    return repositories, repository_id, jobs, provider, worker, source, original


def test_success_retry_idempotency_and_no_source_writes(tmp_path):
    _, repository_id, jobs, _, worker, source, original = _setup(tmp_path)
    first = jobs.enqueue(repository_id)
    assert worker.process_one()
    result = jobs.get(repository_id, first.job_id)
    assert result.status == "COMPLETED"
    assert result.current_stage == "COMPLETED"
    assert result.completed_stages == list(STAGES)
    active = jobs.active[repository_id]
    assert active[1]
    assert source.read_text() == original
    second = jobs.enqueue(repository_id)
    assert worker.process_one()
    assert jobs.get(repository_id, second.job_id).status == "COMPLETED"
    assert jobs.active[repository_id] == active


def test_duplicate_job_failure_and_retry_keep_old_snapshot(tmp_path):
    _, repository_id, jobs, provider, worker, _, _ = _setup(tmp_path)
    first = jobs.enqueue(repository_id)
    assert worker.process_one()
    active = jobs.active[repository_id]
    provider.fail = True
    failing = jobs.enqueue(repository_id)
    try:
        jobs.enqueue(repository_id)
        assert False, "duplicate job should fail"
    except ApplicationError as exc:
        assert exc.code == "INDEX_JOB_ACTIVE"
    assert worker.process_one()
    failed = jobs.get(repository_id, failing.job_id)
    assert failed.status == "FAILED"
    assert failed.error_code == "EMBEDDING_PROVIDER_FAILED"
    assert "private" not in failed.error_message
    assert jobs.active[repository_id] == active
    provider.fail = False
    retry = jobs.enqueue(repository_id)
    assert worker.process_one()
    assert jobs.get(repository_id, retry.job_id).status == "COMPLETED"
    assert jobs.active[repository_id] == active
    assert jobs.get(repository_id, first.job_id).status == "COMPLETED"


def test_job_api_scope_and_invalid_repository(tmp_path):
    repositories, repository_id, jobs, _, _, _, _ = _setup(tmp_path)
    app = FastAPI()
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.include_router(create_router(jobs, repositories))
    client = TestClient(app)
    missing = client.post(f"/api/v1/repositories/{uuid4()}/index")
    assert missing.status_code == 404
    queued = client.post(f"/api/v1/repositories/{repository_id}/index")
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    assert client.post(f"/api/v1/repositories/{repository_id}/index").status_code == 409
    assert client.get(f"/api/v1/repositories/{repository_id}/index/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/v1/repositories/{uuid4()}/index/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/v1/repositories/{repository_id}/index/jobs/latest").json()["job_id"] == job_id
    assert client.get(f"/api/v1/repositories/{uuid4()}/index/jobs/latest").status_code == 404
