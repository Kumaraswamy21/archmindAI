"""Bounded RRF and candidate-pool settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HybridSearchSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    hybrid_rrf_constant: int = Field(default=60, ge=1, le=1000)
    hybrid_candidate_pool: int = Field(default=20, ge=1, le=20)
