"""Read-only source scan and controlled persistence of static relationships."""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.dependency_intelligence.extractor import JavaDependencyExtractor, JavaSourceUnit
from app.modules.dependency_intelligence.persistence import DependencyPersistence
from app.modules.dependency_intelligence.schemas import (
    DependencyListResponse, DependencyScanResponse, Direction, RelationshipType,
)
from app.modules.repository_ingestion.service import RepositoryService


class DependencyIntelligenceService:
    def __init__(
        self, repositories: RepositoryService, persistence: DependencyPersistence,
        parser: JavaParserAdapter | None = None,
        symbols: JavaSymbolExtractor | None = None,
        extractor: JavaDependencyExtractor | None = None,
    ) -> None:
        self._repositories = repositories
        self._persistence = persistence
        self._parser = parser or JavaParserAdapter()
        self._symbols = symbols or JavaSymbolExtractor()
        self._extractor = extractor or JavaDependencyExtractor()

    def scan(self, repository_id: UUID) -> DependencyScanResponse:
        record = self._repositories.registered_record(repository_id)
        if record is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        files = self._repositories.list_files(repository_id)
        units: list[JavaSourceUnit] = []
        for file in files.files:
            if file.language != "java":
                continue
            source = self._repositories.read_file(repository_id, file.path)
            syntax = self._parser.parse_tree(source.content)
            if syntax.tree.root_node.has_error:
                raise ApplicationError(422, "JAVA_SYNTAX_INVALID", "A Java source file contains syntax errors.")
            units.append(JavaSourceUnit(
                file_path=source.file_path, syntax=syntax,
                extracted=self._symbols.extract(syntax),
            ))
        edges = self._extractor.extract(repository_id, units)
        try:
            count = self._persistence.replace_snapshot(record, edges)
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Dependency persistence is unavailable.") from exc
        return DependencyScanResponse(repository_id=repository_id, total_relationships=count)

    def list_relationships(
        self, repository_id: UUID, *, symbol: str | None,
        relationship_type: RelationshipType | None,
        direction: Direction, limit: int,
    ) -> DependencyListResponse:
        try:
            result = self._persistence.list_relationships(
                repository_id, symbol=symbol, relationship_type=relationship_type,
                direction=direction, limit=limit,
            )
        except SQLAlchemyError as exc:
            raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Dependency persistence is unavailable.") from exc
        if result is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        total, relationships = result
        return DependencyListResponse(
            repository_id=repository_id, total_relationships=total,
            relationships=relationships,
        )
