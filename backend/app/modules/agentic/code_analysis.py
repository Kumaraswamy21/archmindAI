"""Code specialist built on the existing retrieval and grounded RAG path."""

from uuid import UUID

from app.modules.agentic.analysis_common import AgentEvidence, AgentResult
from app.modules.rag.schemas import AskRequest
from app.modules.rag.service import RagService
from app.modules.retrieval.rerank_schemas import RerankRequest
from app.modules.retrieval.rerank_service import RerankSearchService


class CodeAnalysisAgent:
    def __init__(
        self, retrieval: RerankSearchService, rag: RagService,
        *, system_version: str | None = None, user_version: str | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._rag = rag
        self._system_version = system_version
        self._user_version = user_version

    def answer(self, repository_id: UUID, question: str, top_k: int) -> AgentResult:
        ranked = self._retrieval.search(RerankRequest(
            repository_id=repository_id, query=question, top_k=top_k,
        ))
        response, context = self._rag.answer(
            AskRequest(repository_id=repository_id, question=question, top_k=top_k),
            reranked=ranked, prompt_name="code_analysis",
            system_version=self._system_version, user_version=self._user_version,
            include_context=True, defer_citation_validation=True,
        )
        evidence = {
            item.evidence_id: AgentEvidence(
                key=f"code:{item.source.chunk_id}",
                repository_id=item.source.repository_id,
                file_path=item.source.file_path,
                start_line=item.source.start_line,
                end_line=item.source.end_line,
                kind="source_code", content=item.source.content,
            )
            for item in context.evidence
        }
        return AgentResult(
            answer=response.answer, citations=response.citations,
            confidence=response.confidence, limitations=response.limitations,
            prompt_versions=response.prompt_versions,
            model_identifier=response.model_identifier,
            evidence_count=max(0, len(ranked.results) - len(response.excluded_evidence_ids)),
            evidence=evidence,
        )

    def collect(self, repository_id: UUID, question: str, top_k: int) -> list[AgentEvidence]:
        ranked = self._retrieval.search(RerankRequest(
            repository_id=repository_id, query=question, top_k=top_k,
        ))
        sources = self._rag.load_sources(repository_id, ranked)
        return [
            AgentEvidence(
                key=f"code:{item.chunk_id}", repository_id=source.repository_id,
                file_path=source.file_path, start_line=source.start_line,
                end_line=source.end_line, kind="source_code",
                content=f"Symbol: {source.qualified_name}\n{source.content}",
            )
            for item in ranked.results
            for source in [sources[item.chunk_id]]
        ]
