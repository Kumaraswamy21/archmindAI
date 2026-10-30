"""Deterministic reranking API contracts."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.retrieval.hybrid_schemas import HybridSearchRequest


class RerankRequest(HybridSearchRequest):
    query: str = Field(
        min_length=1, max_length=2000,
        examples=["Where is payment processing implemented?"],
    )


class RerankResult(BaseModel):
    chunk_id: str
    file_path: str
    original_rank: int = Field(ge=1)
    rerankerank: int = Field(ge=1)
    rerank_score: float
    reranker_version: str


class RerankResponse(BaseModel):
    repository_id: UUID
    reranker: str
    results: list[RerankResult]
