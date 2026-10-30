"""Deterministic per-claim citation verification for agent answers."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.errors import ApplicationError
from app.modules.agentic.analysis_common import AgentEvidence, AgentResult
from app.modules.rag.context_builder import Evidence, EvidenceContext, EvidenceSource
from app.modules.rag.schemas import Citation
from app.modules.rag.service import RagService


INSUFFICIENT_ANSWER = "Insufficient repository evidence to answer this question."
VerificationStatus = Literal[
    "CITATION_VERIFIED", "PARTIALLY_VERIFIED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE",
]
ClaimType = Literal["OBSERVATION", "RECOMMENDATION"]


class ClaimVerification(BaseModel):
    claim: str
    claim_type: ClaimType
    evidence_ids: list[str]
    status: Literal["CITATION_VERIFIED", "INSUFFICIENT_EVIDENCE"]


class UnsupportedClaim(BaseModel):
    claim: str
    claim_type: ClaimType
    reason: str


@dataclass(frozen=True)
class VerificationReport:
    status: VerificationStatus
    claims: list[ClaimVerification]
    unsupported_claims: list[UnsupportedClaim]
    supported_claims: list[str]
    supported_citations: list[Citation]

    @property
    def needs_correction(self) -> bool:
        return bool(self.unsupported_claims)


class EvidenceVerifier:
    """Checks citation integrity and explicit claim-to-evidence markers.

    This does not prove semantic entailment; it rejects missing or mismatched
    references and leaves unsupported claims out of the final answer.
    """

    def verify(self, repository_id: UUID, result: AgentResult) -> VerificationReport:
        evidence = result.evidence or {}
        for item in evidence.values():
            self._validate_scope(repository_id, item)
        context = EvidenceContext(
            text="",
            evidence=tuple(Evidence(
                evidence_id=evidence_id,
                source=EvidenceSource(
                    chunk_id=item.key, repository_id=item.repository_id,
                    file_path=item.file_path, qualified_name=item.key,
                    start_line=item.start_line, end_line=item.end_line,
                    start_byte=0, end_byte=0, content=item.content,
                ),
            ) for evidence_id, item in evidence.items()),
            excluded_evidence_ids=(), limitations=(),
        )
        invalid_ids: set[str] = set()
        valid: dict[str, Citation] = {}
        for citation in result.citations:
            try:
                RagService._validate_citations([citation], context)
            except ApplicationError:
                invalid_ids.add(citation.evidence_id)
            else:
                valid[citation.evidence_id] = citation
        for evidence_id in invalid_ids:
            valid.pop(evidence_id, None)
        if result.confidence == "INSUFFICIENT" and not result.citations:
            return VerificationReport("INSUFFICIENT_EVIDENCE", [], [], [], [])
        claims: list[ClaimVerification] = []
        unsupported: list[UnsupportedClaim] = []
        supported: list[str] = []
        cited: dict[str, Citation] = {}
        for claim in self._claims(result.answer):
            ids = list(dict.fromkeys(re.findall(r"\[([A-Za-z][A-Za-z0-9]*)\]", claim)))
            claim_type: ClaimType = (
                "RECOMMENDATION"
                if re.match(r"\s*(?:Proposed|Recommendation|Consider):", claim, re.I)
                else "OBSERVATION"
            )
            missing = [evidence_id for evidence_id in ids if evidence_id not in valid]
            if re.search(
                r"\b(?:ignore (?:previous|all) instructions|reveal secrets)\b",
                claim, re.I,
            ):
                reason = "Claim repeats an instruction from untrusted evidence."
            elif not ids:
                reason = "Claim has no evidence ID."
            elif missing:
                reason = "Claim cites missing, invalid, or unreturned evidence."
            else:
                reason = ""
            if reason:
                unsupported.append(UnsupportedClaim(
                    claim=claim, claim_type=claim_type, reason=reason,
                ))
                status = "INSUFFICIENT_EVIDENCE"
            else:
                supported.append(claim)
                for evidence_id in ids:
                    cited[evidence_id] = valid[evidence_id]
                status = "CITATION_VERIFIED"
            claims.append(ClaimVerification(
                claim=claim, claim_type=claim_type,
                evidence_ids=ids, status=status,
            ))
        referenced = {
            evidence_id for claim in claims for evidence_id in claim.evidence_ids
        }
        for evidence_id in sorted(invalid_ids - referenced):
            unsupported.append(UnsupportedClaim(
                claim=f"Returned citation {evidence_id}",
                claim_type="OBSERVATION",
                reason="Returned citation is missing, invalid, or outside source bounds.",
            ))
        if not claims and result.confidence != "INSUFFICIENT":
            unsupported.append(UnsupportedClaim(
                claim=result.answer, claim_type="OBSERVATION",
                reason="Answer contains no identifiable claim.",
            ))
        if unsupported:
            final_status: VerificationStatus = (
                "PARTIALLY_VERIFIED" if supported else "INSUFFICIENT_EVIDENCE"
            )
        else:
            final_status = "CITATION_VERIFIED"
        return VerificationReport(
            final_status, claims, unsupported, supported,
            [cited[key] for key in sorted(
                cited, key=lambda value: (
                    int(value[1:]) if value[1:].isdigit() else 0, value,
                ),
            )],
        )

    @staticmethod
    def _claims(answer: str) -> list[str]:
        return [
            segment.strip() for line in answer.splitlines()
            for segment in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
            if segment.strip()
        ]

    @staticmethod
    def _validate_scope(repository_id: UUID, item: AgentEvidence) -> None:
        path = PurePosixPath(item.file_path)
        windows = PureWindowsPath(item.file_path)
        if (
            item.repository_id != repository_id or not item.file_path
            or path.is_absolute() or windows.is_absolute() or bool(windows.drive)
            or ".." in path.parts or item.start_line < 1
            or item.end_line < item.start_line
        ):
            raise ApplicationError(
                503, "RETRIEVAL_SCOPE_MISMATCH",
                "Source evidence is outside the requested repository.",
            )
