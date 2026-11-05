"""Workflow lifecycle, retry policy, cancellation, and API contract."""

from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.retry import is_retryable
from app.errors import ApplicationError
from app.main import create_app
from app.modules.workflow_reliability.runner import WorkflowRunner
from app.modules.workflow_reliability.schemas import WorkflowRunResponse


class MemoryStore:
    def __init__(self):
        self.rows = {}
        self.events = []

    def create(self, *, repository_id, request_id, workflow_type, workflow_id=None, **_):
        run = WorkflowRunResponse(
            workflow_id=workflow_id or uuid4(), request_id=request_id,
            repository_id=repository_id, workflow_type=workflow_type,
            status="PENDING", attempt_count=0,
        )
        self.rows[run.workflow_id] = run
        return run

    def record_attempt(self, workflow_id):
        row = self.rows[workflow_id]
        row.attempt_count += 1
        self.events.append("attempt")
        return row.attempt_count

    def mark_running(self, workflow_id):
        self.rows[workflow_id].status = "RUNNING"

    def cancel_requested(self, workflow_id):
        return self.rows[workflow_id].cancel_requested

    def complete(self, workflow_id):
        self.rows[workflow_id].status = "COMPLETED"

    def fail(self, workflow_id, code):
        self.rows[workflow_id].status = "FAILED"
        self.rows[workflow_id].error_code = code

    def mark_cancelled(self, workflow_id):
        self.rows[workflow_id].status = "CANCELLED"


def test_bounded_transient_retry_and_persisted_attempt_count():
    store = MemoryStore()
    attempts = 0

    def operation(_):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ApplicationError(504, "RAG_PROVIDER_TIMEOUT", "Timed out.")
        return "ok"

    result, identity = WorkflowRunner(store).run(
        repository_id=uuid4(), request_id=uuid4(), workflow_type="chat",
        operation=operation,
    )
    assert result == "ok"
    assert store.rows[identity].status == "COMPLETED"
    assert store.rows[identity].attempt_count == 3


def test_non_retryable_failure_and_retry_limit():
    for error, expected in [
        (ApplicationError(404, "REPOSITORY_NOT_FOUND", "Missing."), 1),
        (ApplicationError(504, "RAG_PROVIDER_TIMEOUT", "Timed out."), 3),
    ]:
        store = MemoryStore()
        count = 0

        def operation(_):
            nonlocal count
            count += 1
            raise error

        with pytest.raises(ApplicationError):
            WorkflowRunner(store).run(repository_id=uuid4(), request_id=uuid4(),
                                      workflow_type="chat", operation=operation)
        assert count == expected
        assert next(iter(store.rows.values())).status == "FAILED"
    assert not is_retryable(ApplicationError(403, "FORBIDDEN", "No access."))


def test_cancel_before_provider_call():
    store = MemoryStore()
    cancelled = Event()
    cancelled.set()
    called = []
    with pytest.raises(Exception):
        WorkflowRunner(store).run(repository_id=uuid4(), request_id=uuid4(),
                                  workflow_type="chat", cancel_event=cancelled,
                                  operation=lambda _: called.append(True))
    assert called == []
    assert next(iter(store.rows.values())).status == "CANCELLED"


def test_database_outage_fails_closed():
    class Outage(MemoryStore):
        def create(self, **_):
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Unavailable.")

    with pytest.raises(ApplicationError) as error:
        WorkflowRunner(Outage()).run(repository_id=uuid4(), request_id=uuid4(),
                                    workflow_type="chat", operation=lambda _: "unexpected")
    assert error.value.code == "DATABASE_UNAVAILABLE"


def test_swagger_includes_workflow_apis():
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/v1/workflows/{workflow_id}"]
    assert "post" in paths["/api/v1/workflows/{workflow_id}/cancel"]
