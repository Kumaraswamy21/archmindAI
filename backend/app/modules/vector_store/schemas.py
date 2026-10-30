"""Embedding API response models."""

from uuid import UUID

from typing import Literal

from pydantic import BaseModel, Field


class EmbeddingGenerationResponse(BaseModel):
    repository_id: UUID
    total_chunks: int = Field(ge=0)
    generated: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    model: str


class IndexStatusResponse(BaseModel):
    repository_id: UUID
    total_chunks: int = Field(ge=0)
    embedded_chunks: int = Field(ge=0)
    pending_chunks: int = Field(ge=0)
    failed_chunks: int = Field(ge=0)
    index_status: Literal["EMPTY", "PENDING", "PARTIAL", "FAILED", "READY"]
