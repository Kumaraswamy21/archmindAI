"""Process-local repository records and duplicate checks."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from app.errors import ApplicationError


@dataclass(frozen=True)
class RepositoryRecord:
    repository_id: UUID
    name: str
    source_alias: str
    root_path: Path
    status: Literal["REGISTERED"]
    registered_at: datetime


class RepositoryRegistry:
    def __init__(self) -> None:
        self._records: dict[UUID, RepositoryRecord] = {}
        self._names: set[str] = set()
        self._aliases: set[str] = set()
        self._lock = Lock()

    def get(self, repository_id: UUID) -> RepositoryRecord | None:
        with self._lock:
            return self._records.get(repository_id)

    def list(self, limit: int, offset: int) -> tuple[int, list[RepositoryRecord]]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda r: (r.registered_at, r.repository_id))
            return len(records), records[offset:offset + limit]

    def register(self, name: str, source_alias: str, root_path: Path) -> RepositoryRecord:
        with self._lock:
            if name in self._names or source_alias in self._aliases:
                raise ApplicationError(
                    status_code=409,
                    code="REPOSITORY_ALREADY_REGISTERED",
                    message="Repository already registered.",
                )

            record = RepositoryRecord(
                repository_id=uuid4(),
                name=name,
                source_alias=source_alias,
                root_path=root_path,
                status="REGISTERED",
                registered_at=datetime.now(timezone.utc),
            )
            self._records[record.repository_id] = record
            self._names.add(name)
            self._aliases.add(source_alias)
            return record
