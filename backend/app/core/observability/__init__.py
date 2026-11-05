"""Fail-open Langfuse tracing for RAG, agents, and Gemini calls."""

from app.core.observability.redaction import filter_metadata, question_fingerprint
from app.core.observability.settings import ObservabilitySettings
from app.core.observability.tracer import (
    NoOpTracer,
    ObservationHandle,
    RecordingTracer,
    Tracer,
    configure_from_settings,
    configure_tracer,
    get_tracer,
    request_trace,
)

__all__ = [
    "NoOpTracer",
    "ObservationHandle",
    "ObservabilitySettings",
    "RecordingTracer",
    "Tracer",
    "configure_from_settings",
    "configure_tracer",
    "filter_metadata",
    "get_tracer",
    "question_fingerprint",
    "request_trace",
]
