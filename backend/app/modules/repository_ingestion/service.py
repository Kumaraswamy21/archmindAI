"""Repository registration rules."""

from pathlib import Path
from uuid import UUID

from app.errors import ApplicationError
from app.modules.repository_ingestion.discovery import discover_source_files
from app.modules.repository_ingestion.reading import read_source_file
from app.modules.repository_ingestion.registry import RepositoryRecord, RepositoryRegistry
from app.modules.repository_ingestion.schemas import (
    RepositoryFile,
    RepositoryFilesResponse,
    RepositoryFileContentResponse,
    RepositoryRegistrationRequest,
    RepositoryLanguageCounts,
    RepositoryScanResponse,
    RepositoryScanSummary,
)
from app.modules.repository_ingestion.security import (
    RepositoryConfiguration,
    resolve_approved_repository,
)


class RepositoryService:
    def __init__(
        self, configuration: RepositoryConfiguration, registry: RepositoryRegistry
    ) -> None:
        self._configuration = configuration
        self._registry = registry

    def register(self, request: RepositoryRegistrationRequest) -> RepositoryRecord:
        configured_path = self._configuration.aliases.get(request.source_alias)
        if configured_path is None:
            raise ApplicationError(
                status_code=404,
                code="REPOSITORY_ALIAS_NOT_FOUND",
                message="Repository source alias not found.",
            )

        root_path = resolve_approved_repository(
            configured_path, self._configuration.approved_roots
        )
        return self._registry.register(request.name, request.source_alias, root_path)

    def registered_record(self, repository_id: UUID) -> RepositoryRecord | None:
        return self._registry.get(repository_id)

    def list_files(self, repository_id: UUID) -> RepositoryFilesResponse:
        record, root = self._get_available_root(repository_id)
        files = discover_source_files(
            root,
            max_files=self._configuration.max_files,
            max_file_size_bytes=self._configuration.max_file_size_bytes,
        )
        return RepositoryFilesResponse(
            repository_id=record.repository_id,
            total_files=len(files),
            files=[RepositoryFile(**file.__dict__) for file in files],
        )

    def read_file(self, repository_id: UUID, path: str) -> RepositoryFileContentResponse:
        record, root = self._get_available_root(repository_id)
        source = read_source_file(
            root, path, max_file_size_bytes=self._configuration.max_file_size_bytes
        )
        return RepositoryFileContentResponse(
            repository_id=record.repository_id,
            file_path=source.file_path,
            language=source.language,
            line_count=source.line_count,
            content=source.content,
        )

    def scan(self, repository_id: UUID) -> RepositoryScanResponse:
        discovered = self.list_files(repository_id)
        counts = {"java": 0, "python": 0, "typescript": 0}
        for file in discovered.files:
            counts[file.language] += 1

        return RepositoryScanResponse(
            repository_id=discovered.repository_id,
            status="COMPLETED",
            summary=RepositoryScanSummary(
                total_files=discovered.total_files,
                total_size_bytes=sum(file.size_bytes for file in discovered.files),
                languages=RepositoryLanguageCounts(**counts),
            ),
        )

    def _get_available_root(self, repository_id: UUID) -> tuple[RepositoryRecord, Path]:
        record = self._registry.get(repository_id)
        if record is None:
            raise ApplicationError(
                status_code=404,
                code="REPOSITORY_NOT_FOUND",
                message="Repository not found.",
            )

        unavailable = ApplicationError(
            status_code=503,
            code="REPOSITORY_UNAVAILABLE",
            message="Registered repository is unavailable.",
        )
        if record.root_path.is_symlink():
            raise unavailable
        try:
            root = resolve_approved_repository(
                record.root_path, self._configuration.approved_roots
            )
        except ApplicationError as exc:
            raise unavailable from exc
        return record, root
