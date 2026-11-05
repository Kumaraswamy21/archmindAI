"""Request, public response, and Gemini structured-output schemas."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


Confidence = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class AskRequest(BaseModel):
    repository_id: UUID
    question: str = Field(
        min_length=1, max_length=2000,
        examples=["How does OrderService create an order?"],
    )
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Question must contain non-whitespace text.")
        return normalized


class Citation(BaseModel):
    evidence_id: str
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class GeneratedAnswer(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[Citation]
    confidence: Confidence
    limitations: list[str]


class PromptVersions(BaseModel):
    name: str
    system: str
    user: str


class AskResponse(BaseModel):
    repository_id: UUID
    status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE"]
    answer: str
    citations: list[Citation]
    confidence: Confidence
    limitations: list[str]
    excluded_evidence_ids: list[str]
    prompt_versions: PromptVersions
    model_identifier: str
    workflow_id: UUID | None = None
