"""Repository registration rules."""

from pathlib import Path
from uuid import UUID
from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.repository_ingestion.discovery import discover_source_files
from app.modules.repository_ingestion.reading import read_source_file
from app.modules.repository_ingestion.registry import RepositoryRecord, RepositoryRegistry
from app.modules.repository_ingestion.persistence import ProjectRecord, ProjectStore
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
        self, configuration: RepositoryConfiguration, registry: RepositoryRegistry,
        store: ProjectStore | None = None,
    ) -> None:
        self._configuration = configuration
        self._registry = registry
        self._store = store

    def attach_store(self, store: ProjectStore) -> None:
        self._store = store

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
        if self._store is None:
            return self._registry.register(request.name, request.source_alias, root_path)
        try:
            project = self._store.create(request.name, request.source_alias)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Project registration is unavailable.") from exc
        return self._record(project, root_path)

    def registered_record(self, repository_id: UUID) -> RepositoryRecord | None:
        if self._store is None:
            return self._registry.get(repository_id)
        project = self.project(repository_id)
        if project is None:
            return None
        path = self._configuration.aliases.get(project.source_alias)
        if path is None:
            return None
        try:
            root = resolve_approved_repository(path, self._configuration.approved_roots)
        except ApplicationError:
            return None
        return self._record(project, root)

    def project(self, repository_id: UUID) -> ProjectRecord | None:
        if self._store is not None:
            try:
                return self._store.get(repository_id)
            except SQLAlchemyError as exc:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Project metadata is unavailable.") from exc
        record = self._registry.get(repository_id)
        return self._metadata(record) if record else None

    def list_projects(self, limit: int, offset: int) -> tuple[int, list[ProjectRecord]]:
        if self._store is not None:
            try:
                return self._store.list(limit, offset)
            except SQLAlchemyError as exc:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Project listing is unavailable.") from exc
        total, records = self._registry.list(limit, offset)
        return total, [self._metadata(record) for record in records]

    def approved_aliases(self) -> list[str]:
        return sorted(alias for alias, path in self._configuration.aliases.items()
                      if self._is_available(path))

    def _is_available(self, path: Path) -> bool:
        try:
            resolve_approved_repository(path, self._configuration.approved_roots)
            return True
        except ApplicationError:
            return False

    @staticmethod
    def _metadata(record: RepositoryRecord) -> ProjectRecord:
        return ProjectRecord(record.repository_id, record.name, record.source_alias,
                             record.status, record.registered_at)

    @staticmethod
    def _record(project: ProjectRecord, root: Path) -> RepositoryRecord:
        return RepositoryRecord(project.repository_id, project.name, project.source_alias,
                                root, "REGISTERED", project.registered_at)

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

    def read_file(self, repository_id: UUID, path: str,
                  start_line: int | None = None, end_line: int | None = None) -> RepositoryFileContentResponse:
        record, root = self._get_available_root(repository_id)
        source = read_source_file(
            root, path, max_file_size_bytes=self._configuration.max_file_size_bytes
        )
        if (start_line is None) != (end_line is None):
            raise ApplicationError(422, "INVALID_LINE_RANGE", "Provide both start_line and end_line.")
        if start_line is not None and end_line is not None:
            if start_line > end_line or end_line > source.line_count:
                raise ApplicationError(416, "SOURCE_LINE_RANGE_INVALID", "Requested source lines are unavailable.")
            selected = source.content.splitlines(keepends=True)[start_line - 1:end_line]
            return RepositoryFileContentResponse(
                repository_id=record.repository_id,
                file_path=source.file_path, language=source.language,
                line_count=len(selected), content="".join(selected),
                start_line=start_line, end_line=end_line,
                total_line_count=source.line_count,
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
        record = self.registered_record(repository_id)
        if record is None:
            if self.project(repository_id) is not None:
                raise ApplicationError(503, "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable.")
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
