"""Keyword search API models."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class KeywordSearchRequest(BaseModel):
    repository_id: UUID
    query: str = Field(min_length=1, max_length=2000, examples=["OrderService"])
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Query must contain non-whitespace text.")
        return normalized


class KeywordSearchResult(BaseModel):
    chunk_id: str
    file_path: str
    symbol_name: str
    start_line: int
    end_line: int
    match_type: Literal["EXACT_SYMBOL", "EXACT_PARENT_SYMBOL", "FULL_TEXT"]
    score: float


class KeywordSearchResponse(BaseModel):
    repository_id: UUID
    results: list[KeywordSearchResult]
