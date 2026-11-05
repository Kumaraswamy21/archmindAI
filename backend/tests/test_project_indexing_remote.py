"""Opt-in complete job against a disposable schema in managed PostgreSQL."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings
from app.db.models import IndexJobRow, RepositoryRow
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.modules.project_indexing.persistence import IndexJobStore
from app.modules.project_indexing.schemas import STAGES
from app.modules.project_indexing.worker import IndexWorker
from app.modules.vector_store.config import EmbeddingSettings


pytestmark = pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_PROJECT_INDEX_REMOTE_TEST") != "1",
    reason="Requires explicit opt-in and managed PostgreSQL credentials.",
)


class DeterministicEmbeddingProvider:
    def __init__(self, settings):
        self.model_identifier = settings.embedding_model
        self.output_dimension = settings.embedding_dimension

    def embed_documents(self, texts):
        return [[0.01] * self.output_dimension for _ in texts]

    def embed_query(self, text):
        return [0.01] * self.output_dimension


def test_complete_index_job_in_isolated_remote_schema(monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    if url.host and "-pooler" in url.host:
        monkeypatch.setenv("DATABASE_URL", url.set(host=url.host.replace("-pooler", "")).render_as_string(hide_password=False))
    schema = "archmind_index_test_" + uuid4().hex[:12]
    admin = create_database_engine(DatabaseSettings())
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'))
    try:
        monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema},public")
        command.upgrade(Config("alembic.ini"), "head")
        sample = Path("evaluation/sample_repository").resolve()
        alias = "isolated-" + uuid4().hex[:10]
        monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(sample)]))
        monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({alias: str(sample)}))
        app = create_app()
        try:
            with app.state.db_engine.connect() as connection:
                assert connection.scalar(text("select current_schema()")) == schema
            settings = EmbeddingSettings(gemini_api_key="deterministic-test-only")
            provider = DeterministicEmbeddingProvider(settings)
            sessions = create_session_factory(app.state.db_engine)
            worker = IndexWorker(IndexJobStore(sessions), app.state.repository_service,
                                 provider, settings)
            with TestClient(app) as client:
                registered = client.post("/api/v1/repositories", json={
                    "name": "Isolated indexing project", "source_alias": alias,
                })
                assert registered.status_code == 201, registered.json()
                repository_id = registered.json()["repository_id"]
                queued = client.post(f"/api/v1/repositories/{repository_id}/index")
                assert queued.status_code == 202, queued.json()
                job_id = queued.json()["job_id"]
                assert client.post(f"/api/v1/repositories/{repository_id}/index").status_code == 409
                assert client.get(f"/api/v1/repositories/{uuid4()}/index/jobs/{job_id}").status_code == 404
                assert worker.process_one()
                assert client.get(
                    f"/api/v1/workflows/{job_id}", params={"repository_id": repository_id},
                ).json()["status"] == "COMPLETED"
                completed = client.get(f"/api/v1/repositories/{repository_id}/index/jobs/{job_id}")
                assert completed.status_code == 200
                assert completed.json()["status"] == "COMPLETED"
                assert completed.json()["completed_stages"] == list(STAGES)
                status = client.get(f"/api/v1/repositories/{repository_id}/index/status")
                assert status.status_code == 200
                assert status.json()["index_status"] == "READY"
                with sessions() as session:
                    snapshot = session.get(RepositoryRow, UUID(repository_id)).active_snapshot_id
                    assert snapshot and len(snapshot) == 64
                # A claimed job whose worker died is reclaimed after its lease expires.
                retry = client.post(f"/api/v1/repositories/{repository_id}/index")
                assert retry.status_code == 202
                retry_id = UUID(retry.json()["job_id"])
                old_claim = worker.jobs.claim()
                assert old_claim and old_claim.job_id == retry_id
                with sessions.begin() as session:
                    session.execute(update(IndexJobRow).where(IndexJobRow.job_id == retry_id).values(
                        lease_until=datetime.now(timezone.utc) - timedelta(seconds=1),
                    ))
                replacement = IndexWorker(IndexJobStore(sessions), app.state.repository_service,
                                          provider, settings)
                assert replacement.process_one()
                with pytest.raises(RuntimeError):
                    worker.jobs.heartbeat(old_claim)
                assert not replacement.process_one()
                assert client.get(f"/api/v1/repositories/{repository_id}/index/jobs/{retry_id}").json()["status"] == "COMPLETED"
                with sessions() as session:
                    assert session.get(RepositoryRow, UUID(repository_id)).active_snapshot_id == snapshot
        finally:
            app.state.db_engine.dispose()
    finally:
        # This schema is uniquely named and created by this test only.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
