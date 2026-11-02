"""Public repository registration schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RepositoryRegistrationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "ecommerce-service", "source_alias": "sample-ecommerce"}]
        },
    )

    name: str = Field(min_length=1, max_length=100)
    source_alias: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isprintable():
            raise ValueError("Project name must contain printable text.")
        return value


class RepositoryRegistrationResponse(BaseModel):
    repository_id: UUID
    name: str
    source_alias: str
    status: Literal["REGISTERED"]
    registered_at: datetime


class RepositoryListResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    repositories: list[RepositoryRegistrationResponse]


class ApprovedAliasesResponse(BaseModel):
    aliases: list[str]


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
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    total_line_count: int | None = Field(default=None, ge=0)


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
