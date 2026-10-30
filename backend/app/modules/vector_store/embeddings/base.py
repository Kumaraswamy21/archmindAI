"""Small provider contract and sanitized failure categories."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    model_identifier: str
    output_dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingProviderError(Exception):
    code = "EMBEDDING_PROVIDER_FAILED"
    retryable = False


class EmbeddingRateLimitError(EmbeddingProviderError):
    code = "EMBEDDING_RATE_LIMITED"
    retryable = True


class EmbeddingTimeoutError(EmbeddingProviderError):
    code = "EMBEDDING_PROVIDER_TIMEOUT"
    retryable = True


class EmbeddingUnavailableError(EmbeddingProviderError):
    code = "EMBEDDING_PROVIDER_UNAVAILABLE"
    retryable = True


class EmbeddingDimensionError(EmbeddingProviderError):
    code = "EMBEDDING_DIMENSION_MISMATCH"
