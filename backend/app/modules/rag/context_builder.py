"""Bounded evidence assembly without source truncation."""

import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID


@dataclass(frozen=True)
class EvidenceSource:
    chunk_id: str
    repository_id: UUID
    file_path: str
    qualified_name: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    content: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: EvidenceSource


@dataclass(frozen=True)
class EvidenceContext:
    text: str
    evidence: tuple[Evidence, ...]
    excluded_evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


def build_evidence_context(
    repository_id: UUID, sources: list[EvidenceSource], max_chars: int
) -> EvidenceContext:
    if max_chars < 1:
        raise ValueError("Context limit must be positive.")
    included: list[Evidence] = []
    serialized: list[str] = []
    excluded: list[str] = []
    limitations: list[str] = []
    occupied = 0
    for rank, source in enumerate(sources, start=1):
        evidence_id = f"E{rank}"
        path = PurePosixPath(source.file_path)
        windows_path = PureWindowsPath(source.file_path)
        if (
            source.repository_id != repository_id
            or path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or source.file_path in {"", "."}
            or ".." in path.parts
            or source.start_line < 1
            or source.end_line < source.start_line
            or source.start_byte < 0
            or source.end_byte < source.start_byte
        ):
            raise ValueError("Evidence source is outside the requested repository or has invalid bounds.")
        if any(
            prior.source.file_path == source.file_path
            and prior.source.start_byte <= source.start_byte
            and prior.source.end_byte >= source.end_byte
            for prior in included
        ):
            excluded.append(evidence_id)
            limitations.append(f"Evidence {evidence_id} was omitted because an earlier span contains it.")
            continue
        record = json.dumps({
            "evidence_id": evidence_id,
            "repository_id": str(repository_id),
            "file_path": source.file_path,
            "qualified_symbol": source.qualified_name,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "content": source.content,
        }, ensure_ascii=False, separators=(",", ":"))
        added = len(record) + (1 if serialized else 0)
        if occupied + added > max_chars:
            excluded.append(evidence_id)
            limitations.append(f"Evidence {evidence_id} was omitted because it exceeds the context size limit.")
            continue
        included.append(Evidence(evidence_id, source))
        serialized.append(record)
        occupied += added
    return EvidenceContext(
        text="\n".join(serialized), evidence=tuple(included),
        excluded_evidence_ids=tuple(excluded), limitations=tuple(limitations),
    )
