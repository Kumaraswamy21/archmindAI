"""Architecture specialist with selective, bounded parallel evidence gathering."""

from concurrent.futures import ThreadPoolExecutor
import re
from uuid import UUID

from app.errors import ApplicationError
from app.modules.agentic.analysis_common import AgentResult, PromptedAnalyzer
from app.modules.agentic.code_analysis import CodeAnalysisAgent
from app.modules.agentic.dependency_analysis import DependencyAnalysisAgent


class ArchitectureAnalysisAgent:
    def __init__(
        self, code: CodeAnalysisAgent, dependency: DependencyAnalysisAgent,
        analyzer: PromptedAnalyzer,
    ) -> None:
        self._code = code
        self._dependency = dependency
        self._analyzer = analyzer

    def answer(self, repository_id: UUID, question: str, top_k: int) -> AgentResult:
        # Two read-only sources are independent; synthesis waits for both.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="archmind-architecture") as pool:
            code_future = pool.submit(self._code.collect, repository_id, question, top_k)
            dependency_future = pool.submit(self._dependency.collect, repository_id, question)
            code_evidence = code_future.result()
            dependency_evidence, limitations = dependency_future.result()
        # The common builder sorts and deduplicates evidence deterministically.
        result = self._analyzer.answer(
            "architecture_analysis", repository_id, question,
            [*code_evidence, *dependency_evidence], limitations=limitations,
        )
        if re.search(
            r"\b(?:we|I|the team|repository|system)\s+(?:have\s+|has\s+)?(?:migrated|replaced|changed)\b"
            r"|\b(?:migration|replacement)\s+(?:is|was|has been)\s+(?:complete|completed|implemented)\b",
            result.answer, re.I,
        ):
            raise ApplicationError(
                502, "UNSUPPORTED_CHANGE_CLAIM",
                "Generated architecture answer claims an unsupported completed change.",
            )
        return result
