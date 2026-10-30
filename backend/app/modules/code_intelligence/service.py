"""Java parsing orchestration over the secure repository reader."""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.java_chunker import JavaAstChunker
from app.modules.code_intelligence.chunking.schemas import (
    ChunkIndexResponse,
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    PersistedChunksResponse,
)
from app.modules.code_intelligence.chunking.java_chunker import excluded_source_spans
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.code_intelligence.schemas import (
    JavaClass,
    JavaMethod,
    JavaParseRequest,
    JavaParseResponse,
    JavaSymbolsResponse,
)
from app.modules.repository_ingestion.service import RepositoryService


class CodeIntelligenceService:
    def __init__(
        self,
        repository_service: RepositoryService,
        parser: JavaParserAdapter,
        symbol_extractor: JavaSymbolExtractor | None = None,
        chunker: JavaAstChunker | None = None,
        persistence: ChunkPersistence | None = None,
    ) -> None:
        self._repository_service = repository_service
        self._parser = parser
        self._symbol_extractor = symbol_extractor or JavaSymbolExtractor()
        self._chunker = chunker or JavaAstChunker()
        self._persistence = persistence

    def parse_java(
        self, repository_id: UUID, request: JavaParseRequest
    ) -> JavaParseResponse:
        source = self._repository_service.read_file(repository_id, request.file_path)
        if source.language != "java":
            raise ApplicationError(
                status_code=400,
                code="UNSUPPORTED_PARSE_LANGUAGE",
                message="Only Java source files can be parsed.",
            )

        parsed = self._parser.parse(source.content)
        return JavaParseResponse(
            file_path=source.file_path,
            language="java",
            has_syntax_errors=parsed.has_syntax_errors,
            classes=[
                JavaClass(
                    name=parsed_class.name,
                    start_line=parsed_class.start_line,
                    end_line=parsed_class.end_line,
                    methods=[
                        JavaMethod(
                            name=method.name,
                            start_line=method.start_line,
                            end_line=method.end_line,
                        )
                        for method in parsed_class.methods
                    ],
                )
                for parsed_class in parsed.classes
            ],
        )

    def extract_symbols(self, repository_id: UUID, file_path: str) -> JavaSymbolsResponse:
        source = self._repository_service.read_file(repository_id, file_path)
        if source.language != "java":
            raise ApplicationError(
                status_code=400,
                code="UNSUPPORTED_PARSE_LANGUAGE",
                message="Only Java source files can be parsed.",
            )

        extracted = self._symbol_extractor.extract(self._parser.parse_tree(source.content))
        return JavaSymbolsResponse(
            file_path=source.file_path,
            package=extracted.package_name,
            imports=list(extracted.imports),
            symbols=list(extracted.symbols),
        )

    def preview_chunks(
        self, repository_id: UUID, request: ChunkPreviewRequest
    ) -> ChunkPreviewResponse:
        source = self._repository_service.read_file(repository_id, request.file_path)
        if source.language != "java":
            raise ApplicationError(
                status_code=400,
                code="UNSUPPORTED_PARSE_LANGUAGE",
                message="Only Java source files can be parsed.",
            )

        syntax = self._parser.parse_tree(source.content)
        extracted = self._symbol_extractor.extract(syntax)
        chunks = self._chunker.chunk(
            repository_id, source.file_path, syntax, extracted, request.max_chunk_chars
        )
        return ChunkPreviewResponse(
            file_path=source.file_path,
            total_chunks=len(chunks),
            chunks=chunks,
            excluded_spans=excluded_source_spans(syntax.source_bytes, chunks),
        )

    def index_chunks(
        self, repository_id: UUID, request: ChunkPreviewRequest
    ) -> ChunkIndexResponse:
        persistence = self._require_persistence()
        record = self._repository_service.registered_record(repository_id)
        if record is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        preview = self.preview_chunks(repository_id, request)
        try:
            count = persistence.replace_file_chunks(record, preview.file_path, preview.chunks)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Chunk persistence is unavailable."
            ) from exc
        return ChunkIndexResponse(
            repository_id=repository_id, file_path=preview.file_path, total_chunks=count
        )

    def list_persisted_chunks(
        self, repository_id: UUID, limit: int, offset: int
    ) -> PersistedChunksResponse:
        persistence = self._require_persistence()
        try:
            result = persistence.list_chunks(repository_id, limit, offset)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Chunk persistence is unavailable."
            ) from exc
        if result is None:
            if self._repository_service.registered_record(repository_id) is None:
                raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
            total, chunks = 0, []
        else:
            total, chunks = result
        return PersistedChunksResponse(
            repository_id=repository_id, total_chunks=total,
            limit=limit, offset=offset, chunks=chunks,
        )

    def _require_persistence(self) -> ChunkPersistence:
        if self._persistence is None:
            raise ApplicationError(
                503, "DATABASE_NOT_CONFIGURED", "Cloud database is not configured."
            )
        return self._persistence
