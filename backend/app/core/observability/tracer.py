"""Tracer implementations: no-op, in-memory recording, and Langfuse export."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator, Literal
from uuid import uuid4

from app.core.observability.client import create_langfuse_client
from app.core.observability.redaction import filter_metadata, question_fingerprint
from app.core.observability.settings import ObservabilitySettings
from app.errors import ApplicationError

_log = logging.getLogger("archmind.observability")

AsType = Literal[
    "span", "generation", "agent", "retriever", "embedding", "evaluator", "chain",
]


@dataclass
class ObservationHandle:
    name: str
    as_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] | None = None
    output: dict[str, Any] | None = None
    remote: Any = None

    def set(self, *, metadata: dict[str, Any] | None = None, output: dict[str, Any] | None = None,
            usage: dict[str, int] | None = None) -> None:
        if metadata:
            self.metadata.update(filter_metadata(metadata))
        if output is not None:
            self.output = filter_metadata(output)
        if usage:
            self.usage = {key: value for key, value in usage.items() if isinstance(value, int)}
        remote = self.remote
        if remote is None:
            return
        try:
            update = {}
            if metadata:
                update["metadata"] = filter_metadata(self.metadata)
            if output is not None:
                update["output"] = self.output
            if usage and self.usage:
                update["usage_details"] = self.usage
            if update and hasattr(remote, "update"):
                remote.update(**update)
        except Exception:
            _log.debug("Langfuse span update failed.", exc_info=True)

    def record_error(self, exc: BaseException) -> None:
        code = getattr(exc, "code", None) or type(exc).__name__
        self.set(metadata={"error_code": str(code), "error_category": str(code)})
        remote = self.remote
        if remote is None:
            return
        try:
            if hasattr(remote, "update"):
                remote.update(level="ERROR", status_message=str(code))
        except Exception:
            _log.debug("Langfuse error annotation failed.", exc_info=True)


class Tracer:
    def span(
        self, name: str, *, as_type: AsType = "span",
        metadata: dict[str, Any] | None = None, session_id: str | None = None,
        model: str | None = None,
    ):
        raise NotImplementedError

    def flush(self) -> None:
        return None

    def enabled(self) -> bool:
        return False


class NoOpTracer(Tracer):
    @contextmanager
    def span(
        self, name: str, *, as_type: AsType = "span",
        metadata: dict[str, Any] | None = None, session_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[ObservationHandle]:
        handle = ObservationHandle(name=name, as_type=as_type, metadata=filter_metadata(metadata))
        yield handle

    def enabled(self) -> bool:
        return False


@dataclass
class RecordedSpan:
    id: str
    name: str
    parent_id: str | None
    as_type: str
    metadata: dict[str, Any]
    session_id: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None
    output: dict[str, Any] | None = None
    error_code: str | None = None


class RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []
        self._stack: ContextVar[tuple[str, ...]] = ContextVar("recording_stack", default=())
        self.client_calls = 0

    def enabled(self) -> bool:
        return True

    @contextmanager
    def span(
        self, name: str, *, as_type: AsType = "span",
        metadata: dict[str, Any] | None = None, session_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[ObservationHandle]:
        self.client_calls += 1
        span_id = uuid4().hex
        parent = self._stack.get()[-1] if self._stack.get() else None
        recorded = RecordedSpan(
            id=span_id, name=name, parent_id=parent, as_type=as_type,
            metadata=filter_metadata(metadata), session_id=session_id, model=model,
        )
        self.spans.append(recorded)
        handle = ObservationHandle(name=name, as_type=as_type, metadata=dict(recorded.metadata))
        token = self._stack.set(self._stack.get() + (span_id,))
        try:
            yield handle
        except Exception as exc:
            handle.record_error(exc)
            recorded.error_code = handle.metadata.get("error_code")
            raise
        finally:
            recorded.metadata.update(handle.metadata)
            recorded.usage = handle.usage
            recorded.output = handle.output
            self._stack.reset(token)


class LangfuseTracer(Tracer):
    def __init__(self, client: Any, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def enabled(self) -> bool:
        return True

    @contextmanager
    def span(
        self, name: str, *, as_type: AsType = "span",
        metadata: dict[str, Any] | None = None, session_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[ObservationHandle]:
        safe = filter_metadata(metadata)
        remote = None
        try:
            kwargs: dict[str, Any] = {"name": name, "as_type": as_type, "metadata": safe or None}
            if as_type in {"generation", "embedding"} and model:
                kwargs["model"] = model
            remote_cm = self._client.start_as_current_observation(**kwargs)
        except Exception:
            _log.debug("Langfuse span start failed.", exc_info=True)
            remote_cm = None
        if remote_cm is None:
            handle = ObservationHandle(name=name, as_type=as_type, metadata=safe)
            yield handle
            return
        try:
            with remote_cm as observation:
                remote = observation
                try:
                    if session_id and hasattr(observation, "update_trace"):
                        observation.update_trace(session_id=session_id, metadata=safe or None)
                except Exception:
                    _log.debug("Langfuse trace update failed.", exc_info=True)
                handle = ObservationHandle(
                    name=name, as_type=as_type, metadata=safe, remote=observation,
                )
                try:
                    yield handle
                except Exception as exc:
                    handle.record_error(exc)
                    raise
        except Exception:
            if remote is None:
                handle = ObservationHandle(name=name, as_type=as_type, metadata=safe)
                yield handle
                return
            raise

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            _log.debug("Langfuse flush failed.", exc_info=True)


_tracer: Tracer = NoOpTracer()


def get_tracer() -> Tracer:
    return _tracer


def configure_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer


def configure_from_settings(settings: ObservabilitySettings | None = None) -> Tracer:
    settings = settings or ObservabilitySettings()
    client = create_langfuse_client(settings)
    tracer: Tracer = LangfuseTracer(client, settings.langfuse_timeout_seconds) if client else NoOpTracer()
    configure_tracer(tracer)
    return tracer


@contextmanager
def request_trace(
    name: str,
    *,
    workflow_type: str,
    repository_id: Any = None,
    request_id: Any = None,
    session_id: Any = None,
    question: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Iterator[ObservationHandle]:
    started = perf_counter()
    metadata = {
        "workflow_type": workflow_type,
        "request_id": str(request_id) if request_id is not None else None,
        "repository_id": str(repository_id) if repository_id is not None else None,
        **question_fingerprint(question),
        **(extra or {}),
    }
    with get_tracer().span(
        name, as_type="chain", metadata=metadata,
        session_id=str(session_id) if session_id else None,
    ) as handle:
        try:
            yield handle
        except ApplicationError as exc:
            handle.record_error(exc)
            raise
        finally:
            elapsed = max(0.0, (perf_counter() - started) * 1000.0)
            handle.set(metadata={"latency_ms": round(elapsed, 3)})
