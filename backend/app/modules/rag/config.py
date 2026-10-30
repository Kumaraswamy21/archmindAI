"""Bounded, opt-in Gemini RAG configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    gemini_api_key: SecretStr
    rag_model_identifier: str = Field(
        default="gemini-2.5-flash", min_length=1, max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    rag_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    rag_max_context_chars: int = Field(default=16000, ge=1, le=100000)
    rag_system_prompt_version: str | None = None
    rag_user_prompt_version: str | None = None
