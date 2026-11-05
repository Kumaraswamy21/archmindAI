"""Public contracts for the repository analysis workflow."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.modules.rag.schemas import AskRequest, Citation, Confidence, PromptVersions
from app.modules.agentic.classifier import Intent
from app.modules.agentic.evidence_verification import (
    ClaimVerification, UnsupportedClaim, VerificationStatus,
)


class AnalyzeRequest(AskRequest):
    """Use the same repository, question, and bounded top-k validation as RAG."""


class WorkflowMetadata(BaseModel):
    completed_nodes: list[Literal[
        "classify_intent", "code_analysis", "dependency_analysis",
        "architecture_analysis", "evidence_aggregation", "evidence_verification",
        "correct_answer", "verify_corrected", "respond", "clarify",
    ]]
    retrieved_count: int
    classified_intent: Intent
    selected_workflow: Literal["code_analysis", "dependency_analysis", "architecture_analysis", "clarification"]
    agents_executed: list[Literal["code_analysis", "dependency_analysis", "architecture_analysis"]]
    evidence_count: int
    execution_duration_ms: float
    classifier_version: str
    routing_reason: str
    classifier_prompt_versions: PromptVersions | None = None
    classifier_model_identifier: str | None = None
    answer_status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "CLARIFICATION_REQUIRED"]
    confidence: Confidence
    limitations: list[str]
    excluded_evidence_ids: list[str]
    prompt_versions: PromptVersions | None = None
    model_identifier: str | None = None
    verification_status: VerificationStatus
    correction_attempts: int


class AnalyzeResponse(BaseModel):
    request_id: UUID
    repository_id: UUID
    answer: str
    citations: list[Citation]
    verification_status: VerificationStatus
    unsupported_claims: list[UnsupportedClaim]
    claim_verifications: list[ClaimVerification]
    agents_executed: list[Literal["code_analysis", "dependency_analysis", "architecture_analysis"]]
    prompt_versions: PromptVersions | None
    workflow_type: Literal["repository_analysis_v1"] = "repository_analysis_v1"
    workflow_metadata: WorkflowMetadata
    workflow_id: UUID | None = None
