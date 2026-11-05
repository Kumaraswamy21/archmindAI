"""Lazy Langfuse client construction. Failures never raise to callers."""

import logging
from typing import Any

from app.core.observability.settings import ObservabilitySettings

_log = logging.getLogger("archmind.observability")


def create_langfuse_client(settings: ObservabilitySettings) -> Any | None:
    if not settings.export_enabled():
        if settings.langfuse_enabled:
            _log.warning("Langfuse is enabled but keys are missing; tracing is disabled.")
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host,
            timeout=max(1, int(settings.langfuse_timeout_seconds)),
            tracing_enabled=True,
        )
    except Exception:
        _log.warning("Langfuse client could not be created; tracing is disabled.", exc_info=True)
        return None
