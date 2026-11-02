"""One graph invocation with repository-scoped citation validation."""

from uuid import UUID, uuid4
from threading import Event
from typing import Callable

from app.errors import ApplicationError
from app.modules.agentic.analysis_common import AgentEvidence
from app.modules.agentic.evidence_verification import EvidenceVerifier
from app.modules.agentic.schemas import AnalyzeRequest, AnalyzeResponse
from app.modules.agentic.workflow import AgenticWorkflow
from app.modules.chat.schemas import ChatCitation, ChatPromptVersions, ChatRequest, ChatResponse
from app.modules.rag.context_builder import Evidence, EvidenceContext, EvidenceSource
from app.modules.rag.service import RagService


class ChatService:
    def __init__(self, workflow: AgenticWorkflow) -> None:
        self._workflow = workflow

    def respond(self, request: ChatRequest, *, request_id: UUID | None = None) -> ChatResponse:
        result, evidence = self._workflow.analyze_with_evidence(
            AnalyzeRequest(repository_id=request.repository_id, question=request.message),
            request_id=request_id,
        )
        return self._map_response(request, result, evidence)

    def stream_respond(
        self, request: ChatRequest, *, request_id: UUID,
        emit: Callable[[str, dict[str, object]], None], cancel_event: Event,
    ) -> ChatResponse:
        result, evidence = self._workflow.stream_with_evidence(
            AnalyzeRequest(repository_id=request.repository_id, question=request.message),
            request_id=request_id, emit=emit, cancel_event=cancel_event,
        )
        return self._map_response(request, result, evidence)

    def _map_response(
        self, request: ChatRequest, result: AnalyzeResponse,
        evidence: dict[str, AgentEvidence],
    ) -> ChatResponse:
        if result.repository_id != request.repository_id:
            raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Analysis left the requested repository.")
        citations = self._citations(request.repository_id, result.citations, evidence)
        metadata = result.workflow_metadata
        if metadata.answer_status == "ANSWERED" and not citations:
            raise ApplicationError(502, "UNSUPPORTED_CITATION", "Answer has no verified source citation.")
        return ChatResponse(
            request_id=result.request_id,
            conversation_id=request.conversation_id or uuid4(),
            repository_id=request.repository_id,
            answer=result.answer,
            citations=citations,
            agents_executed=result.agents_executed,
            classified_intent=metadata.classified_intent,
            verification_status=result.verification_status,
            workflow_status=metadata.answer_status,
            execution_duration_ms=metadata.execution_duration_ms,
            prompt_versions=ChatPromptVersions(
                answer=result.prompt_versions,
                classifier=metadata.classifier_prompt_versions,
            ),
            model_identifier=metadata.model_identifier or metadata.classifier_model_identifier,
        )

    @staticmethod
    def _citations(repository_id: UUID, citations, evidence: dict[str, AgentEvidence]) -> list[ChatCitation]:
        for item in evidence.values():
            EvidenceVerifier._validate_scope(repository_id, item)
        context = EvidenceContext(
            text="",
            evidence=tuple(Evidence(evidence_id, EvidenceSource(
                chunk_id=item.key, repository_id=item.repository_id,
                file_path=item.file_path, qualified_name=item.qualified_symbol or item.key,
                start_line=item.start_line, end_line=item.end_line,
                start_byte=0, end_byte=0, content=item.content,
            )) for evidence_id, item in evidence.items()),
            excluded_evidence_ids=(), limitations=(),
        )
        if citations:
            RagService._validate_citations(citations, context)
        return [ChatCitation(
            evidence_id=citation.evidence_id,
            file_path=citation.file_path,
            start_line=citation.start_line,
            end_line=citation.end_line,
            qualified_symbol=evidence[citation.evidence_id].qualified_symbol,
        ) for citation in citations]
