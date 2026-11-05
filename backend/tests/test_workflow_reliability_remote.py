"""Opt-in lifecycle test in a disposable schema on managed PostgreSQL."""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, update
from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings
from app.db.models import RepositoryRow, WorkflowRunRow
from app.db.session import create_database_engine, create_session_factory
from app.modules.workflow_reliability.store import WorkflowStore


pytestmark = pytest.mark.skipif(
    os.environ.get("ARCHMIND_RUN_WORKFLOW_REMOTE_TEST") != "1",
    reason="Requires explicit opt-in and managed PostgreSQL credentials.",
)


def test_persistence_isolation_cancellation_and_restart_recovery(monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    if url.host and "-pooler" in url.host:
        monkeypatch.setenv("DATABASE_URL", url.set(host=url.host.replace("-pooler", "")).render_as_string(hide_password=False))
    schema = "archmind_workflow_test_" + uuid4().hex[:12]
    admin = create_database_engine(DatabaseSettings())
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'))
    try:
        monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema},public")
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_database_engine(DatabaseSettings())
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("select current_schema()")) == schema
            sessions = create_session_factory(engine)
            repository_id, other_id = uuid4(), uuid4()
            with sessions.begin() as session:
                for identity in (repository_id, other_id):
                    session.add(RepositoryRow(id=identity, name="Test", source_alias=str(identity), status="READY"))
            store = WorkflowStore(sessions)
            run = store.create(repository_id=repository_id, request_id=uuid4(),
                               workflow_type="chat", question="secret source prompt")
            assert store.get(run.workflow_id, other_id) is None
            assert store.get(run.workflow_id, repository_id).status == "PENDING"
            assert "secret source prompt" not in str(store.get(run.workflow_id, repository_id).state)
            store.record_attempt(run.workflow_id)
            store.mark_running(run.workflow_id)
            with sessions.begin() as session:
                session.execute(update(WorkflowRunRow).where(
                    WorkflowRunRow.workflow_id == run.workflow_id,
                ).values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            assert store.recover_stale(older_than_seconds=900) == 1
            recovered = WorkflowStore(sessions)
            assert recovered.get(run.workflow_id, repository_id).error_code == "WORKFLOW_INTERRUPTED"
            pending = store.create(repository_id=repository_id, request_id=uuid4(), workflow_type="chat")
            assert store.request_cancel(pending.workflow_id, repository_id).status == "CANCELLED"
        finally:
            engine.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
