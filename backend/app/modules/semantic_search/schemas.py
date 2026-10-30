"""Public request and response contracts for semantic code search."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SemanticSearchRequest(BaseModel):
    repository_id: UUID
    query: str = Field(min_length=1, max_length=2000, examples=["Where is payment processing implemented?"])
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Query must contain non-whitespace text.")
        return normalized


class SearchResult(BaseModel):
    chunk_id: str
    file_path: str
    symbol_name: str
    start_line: int
    end_line: int
    similarity_score: float
    content: str


class SemanticSearchResponse(BaseModel):
    repository_id: UUID
    query: str
    results: list[SearchResult]
