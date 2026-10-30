"""Pydantic Settings for opt-in Gemini embedding generation."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingIndexSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    embedding_model: str = Field(
        default="gemini-embedding-2", min_length=1, max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    embedding_dimension: int = Field(default=768, ge=128, le=3072)


class EmbeddingSettings(EmbeddingIndexSettings):
    gemini_api_key: SecretStr
    embedding_batch_size: int = Field(default=16, ge=1, le=100)
    embedding_request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    embedding_max_retries: int = Field(default=2, ge=0, le=5)
    embedding_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=5)
