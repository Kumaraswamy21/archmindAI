"""Versioned dataset and retrieval evaluation API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


StrategyName = Literal["semantic", "keyword", "hybrid", "hybrid_reranked"]


class EvaluationRequest(BaseModel):
    repository_id: UUID
    dataset: str = Field(pattern=r"^[a-z][a-z0-9-]{0,99}$", examples=["java-code-search-v1"])
    top_k: int = Field(default=5, ge=5, le=20)


class DatasetQuestion(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    expected_symbols: list[str] = Field(min_length=1)


class EvaluationDataset(BaseModel):
    dataset_id: str
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    repository_snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    questions: list[DatasetQuestion]


class StrategyMetrics(BaseModel):
    strategy: StrategyName
    recall_at_5: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    average_latency_ms: float = Field(ge=0)


class EvaluationResponse(BaseModel):
    repository_id: UUID
    dataset: str
    dataset_version: str
    repository_snapshot_id: str
    query_count: int = Field(ge=0)
    strategies: list[StrategyMetrics]
    evaluated_at: datetime
    embedding_model_identifier: str
    reranker_version: str
