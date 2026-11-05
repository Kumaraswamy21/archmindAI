"""Create, retry, complete, and cancel a workflow run around a callable."""

from threading import Event
from typing import Callable, TypeVar
from uuid import UUID

from app.core.retry import retry_transient
from app.errors import ApplicationError
from app.modules.agentic.workflow import WorkflowCancelled
from app.modules.workflow_reliability import registry
from app.modules.workflow_reliability.schemas import WorkflowRunResponse, WorkflowType

T = TypeVar("T")


class WorkflowRunner:
    def __init__(self, store: object | None = None) -> None:
        self._store = store

    def run(
        self, *, repository_id: UUID, request_id: UUID, workflow_type: WorkflowType,
        question: str | None = None, cancel_event: Event | None = None,
        operation: Callable[[UUID], T],
        workflow_id: UUID | None = None,
        state: dict | None = None,
    ) -> tuple[T, UUID | None]:
        run = self._create(repository_id, request_id, workflow_type, question, workflow_id, state)
        identity = run.workflow_id if run is not None else workflow_id
        if cancel_event is not None and identity is not None:
            registry.register(identity, cancel_event)
        try:
            def attempt() -> T:
                self._raise_if_cancelled(identity, cancel_event)
                if run is not None:
                    self._call("record_attempt", identity)
                    self._call("mark_running", identity)
                    self._raise_if_cancelled(identity, cancel_event)
                return operation(identity or request_id)

            try:
                result = retry_transient(attempt) if run is not None else attempt()
            except WorkflowCancelled:
                self._call("mark_cancelled", identity)
                raise
            except ApplicationError as exc:
                if exc.code == "WORKFLOW_CANCELLED":
                    self._call("mark_cancelled", identity)
                    raise WorkflowCancelled() from exc
                self._call("fail", identity, exc.code)
                raise
            except Exception:
                self._call("fail", identity, "WORKFLOW_FAILED")
                raise
            try:
                self._raise_if_cancelled(identity, cancel_event)
            except ApplicationError as exc:
                if exc.code == "WORKFLOW_CANCELLED":
                    self._call("mark_cancelled", identity)
                    raise WorkflowCancelled() from exc
                raise
            self._call("complete", identity)
            return result, identity
        finally:
            if identity is not None:
                registry.unregister(identity)

    def _create(self, repository_id, request_id, workflow_type, question, workflow_id, state
                ) -> WorkflowRunResponse | None:
        store = self._store
        if store is None:
            return None
        return store.create(
            repository_id=repository_id, request_id=request_id, workflow_type=workflow_type,
            workflow_id=workflow_id, question=question, state=state,
        )

    def _raise_if_cancelled(self, workflow_id: UUID | None, cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ApplicationError(409, "WORKFLOW_CANCELLED", "Workflow cancelled.")
        store = self._store
        if workflow_id is None or store is None:
            return
        if store.cancel_requested(workflow_id):
            raise ApplicationError(409, "WORKFLOW_CANCELLED", "Workflow cancelled.")

    def _call(self, name: str, workflow_id: UUID | None, *args) -> None:
        store = self._store
        if store is None or workflow_id is None:
            return
        method = getattr(store, name, None)
        if method is None:
            return
        method(workflow_id, *args)
