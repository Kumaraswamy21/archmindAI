"""Bounded evidence, shared prompting, and citation checks for specialists."""

import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol
from uuid import UUID

from app.errors import ApplicationError
from app.modules.rag.llm_provider import (
    RagProviderError, RagProviderRateLimited, RagProviderTimeout,
)
from app.modules.rag.schemas import Citation, Confidence, GeneratedAnswer, PromptVersions
from app.prompts.loader import PromptLoader
from app.prompts.registry import PromptError


MAX_EVIDENCE_CHARS = 16000
MAX_EVIDENCE_ITEMS = 40


@dataclass(frozen=True)
class AgentEvidence:
    key: str
    repository_id: UUID
    file_path: str
    start_line: int
    end_line: int
    kind: str
    content: str


@dataclass(frozen=True)
class AgentResult:
    answer: str
    citations: list[Citation]
    confidence: Confidence
    limitations: list[str]
    prompt_versions: PromptVersions
    model_identifier: str
    evidence_count: int
    evidence: dict[str, AgentEvidence] | None = None


class GeneratingProvider(Protocol):
    model_identifier: str

    def generate(self, system_prompt: str, user_prompt: str) -> GeneratedAnswer: ...


def prepare_evidence(
    repository_id: UUID, items: list[AgentEvidence],
    *, max_chars: int = MAX_EVIDENCE_CHARS,
) -> tuple[str, dict[str, AgentEvidence], list[str]]:
    records: list[str] = []
    included: dict[str, AgentEvidence] = {}
    limitations: list[str] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    ordered = sorted(items, key=lambda item: (
        item.file_path, item.start_line, item.end_line, item.kind, item.key,
    ))
    for item in ordered:
        path = PurePosixPath(item.file_path)
        windows = PureWindowsPath(item.file_path)
        if (
            item.repository_id != repository_id or path.is_absolute()
            or windows.is_absolute() or bool(windows.drive)
            or not item.file_path or ".." in path.parts
            or item.start_line < 1 or item.end_line < item.start_line
        ):
            raise ApplicationError(503, "INVALID_EVIDENCE", "Source evidence is invalid.")
        identity = (item.file_path, item.kind, item.start_line, item.end_line, item.content)
        if identity in seen:
            continue
        seen.add(identity)
        evidence_id = f"E{len(included) + 1}"
        serialized = json.dumps({
            "evidence_id": evidence_id,
            "repository_id": str(repository_id),
            "file_path": item.file_path,
            "start_line": item.start_line,
            "end_line": item.end_line,
            "kind": item.kind,
            "content": item.content,
        }, ensure_ascii=False, separators=(",", ":"))
        if len(included) >= MAX_EVIDENCE_ITEMS or sum(map(len, records)) + len(records) + len(serialized) > max_chars:
            limitations.append(f"Evidence {item.key} was omitted by the context limit.")
            continue
        included[evidence_id] = item
        records.append(serialized)
    return "\n".join(records), included, limitations


class PromptedAnalyzer:
    def __init__(
        self, loader: PromptLoader, provider: GeneratingProvider,
        *, versions: dict[str, tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._loader = loader
        self._provider = provider
        self._versions = versions or {}

    def answer(
        self, prompt_name: str, repository_id: UUID, question: str,
        evidence: list[AgentEvidence], *, limitations: list[str] | None = None,
    ) -> AgentResult:
        system_version, user_version = self._versions.get(prompt_name, (None, None))
        try:
            prompt = self._loader.load(
                prompt_name, system_version=system_version, user_version=user_version,
            )
        except PromptError as exc:
            raise ApplicationError(503, "PROMPT_CONFIGURATION_ERROR", "Analysis prompts are unavailable.") from exc
        versions = PromptVersions(
            name=prompt.name, system=prompt.system_version, user=prompt.user_version,
        )
        context, by_id, excluded = prepare_evidence(repository_id, evidence)
        all_limitations = [*(limitations or []), *excluded]
        if not by_id:
            return AgentResult(
                answer="Insufficient repository evidence to answer this question.",
                citations=[], confidence="INSUFFICIENT",
                limitations=[*all_limitations, "No matching repository evidence was available."],
                prompt_versions=versions, model_identifier=self._provider.model_identifier,
                evidence_count=0,
                evidence=by_id,
            )
        try:
            system_text, user_text = prompt.render(
                repository_id=str(repository_id), question=question,
                evidence_context=context,
            )
        except PromptError as exc:
            raise ApplicationError(503, "PROMPT_CONFIGURATION_ERROR", "Analysis prompts are unavailable.") from exc
        try:
            generated = self._provider.generate(system_text, user_text)
        except RagProviderError as exc:
            status = 429 if isinstance(exc, RagProviderRateLimited) else 504 if isinstance(exc, RagProviderTimeout) else 502
            raise ApplicationError(status, exc.code, "Analysis generation failed.") from exc
        if generated.confidence == "INSUFFICIENT":
            if generated.citations:
                raise ApplicationError(502, "UNSUPPORTED_CITATION", "Generated answer has unsupported citations.")
            return AgentResult(
                answer="Insufficient repository evidence to answer this question.",
                citations=[], confidence="INSUFFICIENT",
                limitations=[*generated.limitations, *all_limitations],
                prompt_versions=versions, model_identifier=self._provider.model_identifier,
                evidence_count=len(by_id),
                evidence=by_id,
            )
        return AgentResult(
            answer=generated.answer, citations=generated.citations,
            confidence=generated.confidence,
            limitations=[*generated.limitations, *all_limitations],
            prompt_versions=versions, model_identifier=self._provider.model_identifier,
            evidence_count=len(by_id),
            evidence=by_id,
        )
