"""Hybrid search API contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.retrieval.schemas import KeywordSearchRequest


class HybridSearchRequest(KeywordSearchRequest):
    query: str = Field(
        min_length=1, max_length=2000,
        examples=["How are orders processed?"],
    )


class HybridSearchResult(BaseModel):
    chunk_id: str
    file_path: str
    qualified_name: str
    rrf_score: float
    retrieval_sources: list[Literal["semantic", "keyword"]]
    source_ranks: dict[Literal["semantic", "keyword"], int]


class HybridSearchResponse(BaseModel):
    repository_id: UUID
    query: str
    results: list[HybridSearchResult]
