"""Stable chat request, citation, and response contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.agentic.evidence_verification import VerificationStatus
from app.modules.agentic.classifier import Intent
from app.modules.rag.schemas import PromptVersions


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: UUID
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must contain non-whitespace text.")
        return value


class ChatCitation(BaseModel):
    evidence_id: str
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    qualified_symbol: str | None = None


class ChatPromptVersions(BaseModel):
    answer: PromptVersions | None
    classifier: PromptVersions | None


class ChatResponse(BaseModel):
    request_id: UUID
    conversation_id: UUID
    repository_id: UUID
    answer: str
    citations: list[ChatCitation]
    agents_executed: list[Literal["code_analysis", "dependency_analysis", "architecture_analysis"]]
    classified_intent: Intent
    verification_status: VerificationStatus
    workflow_status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "CLARIFICATION_REQUIRED"]
    execution_duration_ms: float = Field(ge=0)
    prompt_versions: ChatPromptVersions
    model_identifier: str | None
