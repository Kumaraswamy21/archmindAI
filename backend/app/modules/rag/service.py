"""Retrieve, assemble, generate, and validate a grounded answer."""

from sqlalchemy.exc import SQLAlchemyError

from app.core.observability import get_tracer
from app.errors import ApplicationError
from app.modules.rag.config import RagSettings
from app.modules.rag.context_builder import EvidenceContext, build_evidence_context
from app.modules.rag.llm_provider import (
    AnswerProvider, RagProviderError, RagProviderRateLimited, RagProviderTimeout,
)
from app.modules.rag.persistence import RagEvidenceStore
from app.modules.rag.schemas import AskRequest, AskResponse, Citation, PromptVersions
from app.modules.retrieval.rerank_schemas import RerankRequest
from app.modules.retrieval.rerank_schemas import RerankResponse
from app.modules.retrieval.rerank_service import RerankSearchService
from app.prompts.loader import PromptLoader
from app.prompts.registry import PromptError


INSUFFICIENT_ANSWER = "Insufficient repository evidence to answer this question."


class RagService:
    def __init__(
        self,
        rerank_service: RerankSearchService,
        evidence_store: RagEvidenceStore,
        prompt_loader: PromptLoader,
        provider: AnswerProvider,
        settings: RagSettings,
    ) -> None:
        self._rerank_service = rerank_service
        self._evidence_store = evidence_store
        self._prompt_loader = prompt_loader
        self._provider = provider
        self._settings = settings

    def answer(
        self, request: AskRequest, *, reranked: RerankResponse | None = None,
        prompt_name: str = "rag_answer",
        system_version: str | None = None, user_version: str | None = None,
        include_context: bool = False,
        defer_citation_validation: bool = False,
    ) -> AskResponse | tuple[AskResponse, EvidenceContext]:
        try:
            prompt = self._prompt_loader.load(
                prompt_name,
                system_version=system_version if system_version is not None else (
                    self._settings.rag_system_prompt_version if prompt_name == "rag_answer" else None
                ),
                user_version=user_version if user_version is not None else (
                    self._settings.rag_user_prompt_version if prompt_name == "rag_answer" else None
                ),
            )
        except PromptError as exc:
            raise ApplicationError(503, "PROMPT_CONFIGURATION_ERROR", "Answer prompts are unavailable.") from exc
        versions = PromptVersions(
            name=prompt.name, system=prompt.system_version, user=prompt.user_version,
        )
        if reranked is None:
            reranked = self._rerank_service.search(RerankRequest(
                repository_id=request.repository_id, query=request.question,
                top_k=request.top_k,
            ))
        sources = self.load_sources(request.repository_id, reranked)
        try:
            context = build_evidence_context(
                request.repository_id,
                [sources[item.chunk_id] for item in reranked.results],
                self._settings.rag_max_context_chars,
            )
        except ValueError as exc:
            raise ApplicationError(503, "INVALID_EVIDENCE", "Source evidence is invalid.") from exc
        if not context.evidence:
            response = self._insufficient(
                request, versions, context,
                "No source evidence was available within the configured context limit.",
            )
            return (response, context) if include_context else response
        try:
            system_prompt, user_prompt = prompt.render(
                repository_id=str(request.repository_id),
                question=request.question,
                evidence_context=context.text,
            )
        except PromptError as exc:
            raise ApplicationError(503, "PROMPT_CONFIGURATION_ERROR", "Answer prompts are unavailable.") from exc
        try:
            with get_tracer().span("rag.answer", metadata={
                "prompt_name": versions.name,
                "system_prompt_version": versions.system,
                "user_prompt_template_version": versions.user,
                "generation_model": self._provider.model_identifier,
                "repository_id": str(request.repository_id),
            }):
                generated = self._provider.generate(system_prompt, user_prompt)
        except RagProviderError as exc:
            status = 429 if isinstance(exc, RagProviderRateLimited) else 504 if isinstance(exc, RagProviderTimeout) else 502
            raise ApplicationError(status, exc.code, "Answer generation failed.") from exc
        if generated.confidence == "INSUFFICIENT":
            discarded = (
                ["Model citations were discarded because evidence was insufficient."]
                if generated.citations else []
            )
            response = self._insufficient(
                request, versions, context, *generated.limitations, *discarded,
            )
            return (response, context) if include_context else response
        if not defer_citation_validation:
            self._validate_citations(generated.citations, context)
        response = AskResponse(
            repository_id=request.repository_id,
            status="ANSWERED",
            answer=generated.answer,
            citations=generated.citations,
            confidence=generated.confidence,
            limitations=[*generated.limitations, *context.limitations],
            excluded_evidence_ids=list(context.excluded_evidence_ids),
            prompt_versions=versions,
            model_identifier=self._provider.model_identifier,
        )
        return (response, context) if include_context else response

    def load_sources(self, repository_id, reranked: RerankResponse):
        """Validate and load current evidence for a repository-scoped retrieval result."""
        if reranked.repository_id != repository_id:
            raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Evidence changed during retrieval.")
        ids = {item.chunk_id for item in reranked.results}
        try:
            sources = self._evidence_store.get_many(repository_id, ids)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Source evidence is unavailable.") from exc
        if set(sources) != ids or any(
            sources[item.chunk_id].file_path != item.file_path
            for item in reranked.results
        ):
            raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Evidence changed during retrieval.")
        return sources

    def _insufficient(
        self, request: AskRequest, versions: PromptVersions,
        context: EvidenceContext, *extra_limitations: str,
    ) -> AskResponse:
        return AskResponse(
            repository_id=request.repository_id,
            status="INSUFFICIENT_EVIDENCE",
            answer=INSUFFICIENT_ANSWER,
            citations=[], confidence="INSUFFICIENT",
            limitations=[*extra_limitations, *context.limitations],
            excluded_evidence_ids=list(context.excluded_evidence_ids),
            prompt_versions=versions,
            model_identifier=self._provider.model_identifier,
        )

    @staticmethod
    def _validate_citations(citations: list[Citation], context: EvidenceContext) -> None:
        by_id = {item.evidence_id: item.source for item in context.evidence}
        if not citations:
            raise ApplicationError(502, "UNSUPPORTED_CITATION", "Generated answer lacks source citations.")
        for citation in citations:
            source = by_id.get(citation.evidence_id)
            if (
                source is None
                or citation.file_path != source.file_path
                or citation.start_line < source.start_line
                or citation.end_line > source.end_line
                or citation.start_line > citation.end_line
            ):
                raise ApplicationError(502, "UNSUPPORTED_CITATION", "Generated answer has unsupported citations.")
