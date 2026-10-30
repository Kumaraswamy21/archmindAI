"""Public repository registration schemas."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RepositoryRegistrationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "ecommerce-service", "source_alias": "sample-ecommerce"}]
        },
    )

    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source_alias: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RepositoryRegistrationResponse(BaseModel):
    repository_id: UUID
    name: str
    status: Literal["REGISTERED"]


class RepositoryFile(BaseModel):
    path: str
    language: Literal["java", "python", "typescript"]
    size_bytes: int


class RepositoryFilesResponse(BaseModel):
    repository_id: UUID
    total_files: int
    files: list[RepositoryFile]


class RepositoryFileContentResponse(BaseModel):
    repository_id: UUID
    file_path: str
    language: Literal["java", "python", "typescript"]
    line_count: int
    content: str


class RepositoryLanguageCounts(BaseModel):
    java: int = Field(ge=0)
    python: int = Field(ge=0)
    typescript: int = Field(ge=0)


class RepositoryScanSummary(BaseModel):
    total_files: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    languages: RepositoryLanguageCounts


class RepositoryScanResponse(BaseModel):
    repository_id: UUID
    status: Literal["COMPLETED"]
    summary: RepositoryScanSummary


class RequestValidationErrorResponse(BaseModel):
    """FastAPI's default request-validation envelope for OpenAPI documentation."""

    detail: list[dict[str, Any]]
