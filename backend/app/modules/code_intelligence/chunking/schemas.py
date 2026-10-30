"""Public chunk preview schemas."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChunkPreviewRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"file_path": "src/main/java/com/example/OrderService.java", "max_chunk_chars": 1500}]
        },
    )

    file_path: str = Field(min_length=1)
    max_chunk_chars: int | None = Field(default=None, ge=1, le=1_000_000)


class JavaChunk(BaseModel):
    chunk_id: str
    repository_id: UUID
    file_path: str
    language: Literal["java"]
    chunk_type: Literal["method", "constructor", "class", "method_part", "constructor_part", "class_part"]
    parent_chunk_id: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    split_strategy: Literal["ast", "fallback"] | None = None
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    symbol_name: str
    qualified_name: str
    parent_symbol: str | None
    package_name: str | None
    imports: list[str]
    annotations: list[str]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str
    contextual_header: str


class ChunkPreviewResponse(BaseModel):
    file_path: str
    total_chunks: int = Field(ge=0)
    chunks: list[JavaChunk]
    excluded_spans: list["ExcludedSourceSpan"] = Field(default_factory=list)


class ExcludedSourceSpan(BaseModel):
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    reason: Literal["outside_chunked_declarations"]


class ChunkIndexResponse(BaseModel):
    repository_id: UUID
    file_path: str
    total_chunks: int = Field(ge=0)


class PersistedChunksResponse(BaseModel):
    repository_id: UUID
    total_chunks: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    chunks: list[JavaChunk]
