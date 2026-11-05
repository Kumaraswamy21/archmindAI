"""Bounded retries for transient provider and database failures."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError

T = TypeVar("T")

RETRYABLE_CODES = {
    "RAG_PROVIDER_TIMEOUT", "RAG_PROVIDER_RATE_LIMITED",
    "DATABASE_UNAVAILABLE", "EMBEDDING_RATE_LIMITED",
    "EMBEDDING_PROVIDER_TIMEOUT", "EMBEDDING_PROVIDER_UNAVAILABLE",
}
NON_RETRYABLE_CODES = {
    "REPOSITORY_NOT_FOUND", "VALIDATION_ERROR", "UNSUPPORTED_CITATION",
    "JAVA_SYNTAX_INVALID", "SOURCE_CHANGED", "CLASSIFIER_INVALID_RESPONSE",
    "INDEX_JOB_ACTIVE", "INDEX_JOB_CANCELLED", "WORKFLOW_NOT_CANCELLABLE",
    "WORKFLOW_NOT_FOUND", "MANAGED_INDEX_REQUIRED",
}
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.25
BACKOFF_CAP_SECONDS = 5.0


def is_retryable(exc: BaseException) -> bool:
    from app.modules.rag.llm_provider import RagProviderError, RagProviderRateLimited, RagProviderTimeout
    from app.modules.vector_store.embeddings.base import EmbeddingProviderError
    if isinstance(exc, ApplicationError):
        if exc.code in NON_RETRYABLE_CODES or exc.status_code in {400, 401, 403, 404, 409, 422}:
            return False
        return exc.status_code in {429, 504} or exc.code in RETRYABLE_CODES
    if isinstance(exc, (RagProviderTimeout, RagProviderRateLimited)):
        return True
    if isinstance(exc, RagProviderError):
        return False
    if isinstance(exc, EmbeddingProviderError):
        return bool(getattr(exc, "retryable", False))
    if isinstance(exc, OperationalError):
        return True
    return False


def retry_transient(operation: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep,
                    max_attempts: int = MAX_ATTEMPTS,
                    backoff_seconds: float = BACKOFF_SECONDS) -> T:
    last: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            last = exc
            if not is_retryable(exc) or attempt == max_attempts - 1:
                raise
            sleep(min(backoff_seconds * (2 ** attempt), BACKOFF_CAP_SECONDS))
    raise last if last is not None else RuntimeError("Retry failed.")
