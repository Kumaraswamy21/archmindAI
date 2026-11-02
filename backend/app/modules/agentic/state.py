"""Typed, invocation-local LangGraph state. No checkpointer is configured."""

from typing import Callable, TypedDict
from threading import Event
from uuid import UUID

from app.modules.rag.schemas import Citation
from app.modules.retrieval.rerank_schemas import RerankResponse
from app.modules.agentic.classifier import IntentDecision
from app.modules.agentic.analysis_common import AgentResult
from app.modules.agentic.analysis_common import AgentEvidence
from app.modules.agentic.evidence_verification import (
    ClaimVerification, UnsupportedClaim, VerificationReport,
)


class WorkflowState(TypedDict):
    request_id: UUID
    repository_id: UUID
    question: str
    top_k: int
    intent_decision: IntentDecision | None
    retrieved_evidence: RerankResponse | None
    analysis: AgentResult | None
    evidence: dict[str, AgentEvidence]
    verification: VerificationReport | None
    unsupported_claims: list[UnsupportedClaim]
    claim_verifications: list[ClaimVerification]
    correction_attempts: int
    answer: str | None
    citations: list[Citation]
    errors: list[str]
    workflow_metadata: dict[str, object]
    progress_sink: Callable[[str, dict[str, object]], None] | None
    cancel_event: Event | None
