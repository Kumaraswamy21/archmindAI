"""Versioned dataset and retrieval evaluation API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


StrategyName = Literal["semantic", "keyword", "hybrid", "hybrid_reranked"]


class EvaluationRequest(BaseModel):
    repository_id: UUID
    dataset: str = Field(
        pattern=r"^[a-z][a-z0-9-]{0,99}$", examples=["java-code-search-v1"]
    )
    top_k: int = Field(default=5, ge=5, le=20)


class DatasetQuestion(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    expected_symbols: list[str] = Field(min_length=1)


class EvaluationDataset(BaseModel):
    dataset_id: str
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    repository_snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    questions: list[DatasetQuestion]


class StrategyMetrics(BaseModel):
    strategy: StrategyName
    recall_at_5: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    average_latency_ms: float = Field(ge=0)


class EvaluationResponse(BaseModel):
    repository_id: UUID
    dataset: str
    dataset_version: str
    repository_snapshot_id: str
    query_count: int = Field(ge=0)
    strategies: list[StrategyMetrics]
    evaluated_at: datetime
    embedding_model_identifier: str
    reranker_version: str


AgentIntent = Literal[
    "CODE_LOOKUP",
    "CODE_EXPLANATION",
    "DEPENDENCY_ANALYSIS",
    "ARCHITECTURE_ANALYSIS",
    "UNKNOWN",
]
AgentRoute = Literal[
    "code_analysis", "dependency_analysis", "architecture_analysis", "clarification"
]
AgentCategory = Literal[
    "code_lookup",
    "method_explanation",
    "dependency_analysis",
    "architecture_analysis",
    "change_impact",
    "insufficient_evidence",
]


class AgentDatasetQuestion(BaseModel):
    id: str = Field(pattern=r"^[A-Z][0-9]{2}$")
    category: AgentCategory
    question: str = Field(min_length=1, max_length=2000)
    expected_intent: AgentIntent
    expected_workflow: AgentRoute
    expect_abstention: bool


class AgentEvaluationDataset(BaseModel):
    dataset_id: str
    version: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
    repository_snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    questions: list[AgentDatasetQuestion] = Field(min_length=1, max_length=20)


class AgentEvaluationRequest(BaseModel):
    repository_id: UUID
    dataset: str = Field(
        pattern=r"^[a-z][a-z0-9-]{0,99}$", examples=["java-spring-agent-v1"]
    )
    top_k: int = Field(default=5, ge=1, le=20)


class AgentQuestionResult(BaseModel):
    question_id: str
    category: AgentCategory
    status: Literal["COMPLETED", "FAILED"]
    error_code: str | None = None
    classified_intent: AgentIntent | None = None
    selected_workflow: AgentRoute | None = None
    answer_status: str | None = None
    verification_status: str | None = None
    citation_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    latency_ms: float = Field(ge=0)


class AgentEvaluationMetrics(BaseModel):
    intent_accuracy: float = Field(ge=0, le=1)
    routing_accuracy: float = Field(ge=0, le=1)
    workflow_completion_rate: float = Field(ge=0, le=1)
    workflow_failure_rate: float = Field(ge=0, le=1)
    average_end_to_end_latency_ms: float = Field(ge=0)
    citation_validity_rate: float | None = Field(default=None, ge=0, le=1)
    claim_support_rate: float | None = Field(default=None, ge=0, le=1)
    unsupported_claim_rate: float | None = Field(default=None, ge=0, le=1)
    correct_abstention_rate: float | None = Field(default=None, ge=0, le=1)


class AgentEvaluationResponse(BaseModel):
    repository_id: UUID
    dataset: str
    dataset_version: str
    repository_snapshot_id: str
    evaluated_at: datetime
    query_count: int = Field(ge=1)
    embedding_model_identifier: str
    generation_model_identifier: str
    retrieval_configuration: dict[str, int]
    reranker_version: str
    classifier_version: str
    prompt_versions: dict[str, dict[str, str]]
    metrics: AgentEvaluationMetrics
    questions: list[AgentQuestionResult]
