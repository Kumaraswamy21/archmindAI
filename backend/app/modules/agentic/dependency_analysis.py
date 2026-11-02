"""Dependency specialist over persisted repository-scoped static edges."""

import re
from uuid import UUID

from app.errors import ApplicationError
from app.modules.agentic.analysis_common import AgentEvidence, AgentResult, PromptedAnalyzer
from app.modules.dependency_intelligence.service import DependencyIntelligenceService


_COMMON = {
    "what", "where", "which", "when", "how", "does", "will", "would", "could",
    "explain", "show", "find", "depends", "dependencies", "dependency", "direct",
    "callers", "callees", "calls", "changes", "change", "breaks", "impact",
    "module", "service", "class", "method", "the", "from", "with", "replace",
    "needed", "need", "are", "for", "and", "this", "that",
}
MAX_EDGE_CANDIDATES = 200


class DependencyAnalysisAgent:
    def __init__(
        self, dependencies: DependencyIntelligenceService, analyzer: PromptedAnalyzer,
    ) -> None:
        self._dependencies = dependencies
        self._analyzer = analyzer

    def collect(self, repository_id: UUID, question: str) -> tuple[list[AgentEvidence], list[str]]:
        response = self._dependencies.list_relationships(
            repository_id, symbol=None, relationship_type=None,
            direction="both", limit=MAX_EDGE_CANDIDATES,
        )
        if response.repository_id != repository_id:
            raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Dependencies changed during retrieval.")
        terms = [
            word.casefold() for word in re.findall(r"\b[A-Za-z][A-Za-z0-9_]*\b", question)
            if len(word) >= 4 and word.casefold() not in _COMMON
        ]
        # Keep only edges mentioning a requested identifier or meaningful topic.
        relevant = [
            edge for edge in response.relationships
            if terms and any(
                term in edge.source_symbol.casefold() or term in edge.target_symbol.casefold()
                for term in terms
            )
        ]
        relevant.sort(key=lambda edge: (
            edge.file_path, edge.start_line, edge.end_line, edge.relationship_id,
        ))
        limitations = []
        if response.total_relationships > MAX_EDGE_CANDIDATES:
            limitations.append(
                f"Only the first {MAX_EDGE_CANDIDATES} persisted relationships were inspected."
            )
        return [
            AgentEvidence(
                key=f"dependency:{edge.relationship_id}",
                repository_id=edge.repository_id,
                file_path=edge.file_path,
                start_line=edge.start_line, end_line=edge.end_line,
                kind="static_dependency",
                content=(
                    f"{edge.source_symbol} {edge.relationship_type} {edge.target_symbol}; "
                    f"resolution_status={edge.resolution_status}"
                ),
                qualified_symbol=edge.source_symbol,
            )
            for edge in relevant
        ], limitations

    def answer(self, repository_id: UUID, question: str) -> AgentResult:
        evidence, limitations = self.collect(repository_id, question)
        result = self._analyzer.answer(
            "dependency_analysis", repository_id, question,
            evidence, limitations=limitations,
        )
        if (
            evidence
            and all("resolution_status=UNRESOLVED" in item.content for item in evidence)
            and result.confidence != "INSUFFICIENT"
            and not any(phrase in result.answer.casefold() for phrase in (
                "unresolved", "unconfirmed", "not confirmed", "no confirmed",
                "uncertain", "cannot establish", "not established",
            ))
        ):
            raise ApplicationError(
                502, "UNSUPPORTED_DEPENDENCY_CLAIM",
                "Generated dependency answer treats unresolved edges as confirmed.",
            )
        return result
