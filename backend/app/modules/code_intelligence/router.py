"""Java parsing API."""

from uuid import UUID

from fastapi import APIRouter, Query

from app.errors import ErrorResponse
from app.modules.code_intelligence.chunking.schemas import (
    ChunkIndexResponse,
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    PersistedChunksResponse,
)
from app.modules.code_intelligence.schemas import (
    JavaParseRequest,
    JavaParseResponse,
    JavaSymbolsResponse,
)
from app.modules.code_intelligence.service import CodeIntelligenceService


def create_router(service: CodeIntelligenceService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/repositories", tags=["code-intelligence"])

    @router.post(
        "/{repository_id}/parse",
        response_model=JavaParseResponse,
        summary="Parse a Java source file",
        description="Extract top-level Java classes and their direct methods from a registered repository file.",
        responses={
            400: {
                "model": ErrorResponse,
                "description": "Unsupported source type or parse language.",
                "content": {
                    "application/json": {
                        "examples": {
                            "unsupported_type": {
                                "value": {
                                    "error": {
                                        "code": "UNSUPPORTED_SOURCE_FILE",
                                        "message": "Unsupported source file type.",
                                    }
                                }
                            },
                            "unsupported_language": {
                                "value": {
                                    "error": {
                                        "code": "UNSUPPORTED_PARSE_LANGUAGE",
                                        "message": "Only Java source files can be parsed.",
                                    }
                                }
                            },
                        }
                    }
                },
            },
            403: _error_documentation(
                "INVALID_SOURCE_PATH", "Source file path is not permitted."
            ),
            404: {
                "model": ErrorResponse,
                "description": "Repository or source file not found.",
                "content": {
                    "application/json": {
                        "examples": {
                            "unknown_repository": {
                                "value": {
                                    "error": {
                                        "code": "REPOSITORY_NOT_FOUND",
                                        "message": "Repository not found.",
                                    }
                                }
                            },
                            "missing_file": {
                                "value": {
                                    "error": {
                                        "code": "SOURCE_FILE_NOT_FOUND",
                                        "message": "Source file not found.",
                                    }
                                }
                            },
                        }
                    }
                },
            },
            413: _error_documentation(
                "SOURCE_FILE_TOO_LARGE", "Source file exceeds the size limit."
            ),
            422: {
                "model": ErrorResponse,
                "description": "Invalid UTF-8 or invalid request parameters.",
                "content": {
                    "application/json": {
                        "examples": {
                            "invalid_utf8": {
                                "value": {
                                    "error": {
                                        "code": "FILE_ENCODING_INVALID",
                                        "message": "Source file is not valid UTF-8.",
                                    }
                                }
                            },
                            "invalid_request": {"value": {"detail": []}},
                        }
                    }
                },
            },
            503: _error_documentation(
                "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable."
            ),
        },
    )
    def parse_java_file(repository_id: UUID, request: JavaParseRequest) -> JavaParseResponse:
        return service.parse_java(repository_id, request)

    @router.get(
        "/{repository_id}/symbols",
        response_model=JavaSymbolsResponse,
        summary="Extract Java symbols",
        description="Return package, imports, classes, interfaces, methods, constructors, and source context from a Java file.",
        responses={
            400: {
                "model": ErrorResponse,
                "description": "Unsupported source type or parse language.",
            },
            403: _error_documentation(
                "INVALID_SOURCE_PATH", "Source file path is not permitted."
            ),
            404: {
                "model": ErrorResponse,
                "description": "Repository or source file not found.",
            },
            413: _error_documentation(
                "SOURCE_FILE_TOO_LARGE", "Source file exceeds the size limit."
            ),
            422: {
                "model": ErrorResponse,
                "description": "Invalid UTF-8 or invalid request parameters.",
            },
            503: _error_documentation(
                "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable."
            ),
        },
    )
    def extract_java_symbols(
        repository_id: UUID,
        file_path: str = Query(
            ...,
            description="Repository-relative Java source file path.",
            examples=["src/main/java/com/example/OrderService.java"],
        ),
    ) -> JavaSymbolsResponse:
        return service.extract_symbols(repository_id, file_path)

    @router.post(
        "/{repository_id}/chunks/preview",
        response_model=ChunkPreviewResponse,
        summary="Preview AST-aware Java chunks",
        description="Preview Java declaration chunks with structural metadata. Oversized declarations split at AST boundaries, with marked exact-text fallback parts. Unemitted source ranges are reported; no chunks are stored.",
        responses={
            400: {"model": ErrorResponse, "description": "Unsupported source type or parse language."},
            403: _error_documentation("INVALID_SOURCE_PATH", "Source file path is not permitted."),
            404: {"model": ErrorResponse, "description": "Repository or source file not found."},
            413: _error_documentation("SOURCE_FILE_TOO_LARGE", "Source file exceeds the size limit."),
            422: {
                "model": ErrorResponse,
                "description": "Invalid UTF-8 or invalid request parameters.",
            },
            503: _error_documentation("REPOSITORY_UNAVAILABLE", "Registered repository is unavailable."),
        },
    )
    def preview_java_chunks(
        repository_id: UUID, request: ChunkPreviewRequest
    ) -> ChunkPreviewResponse:
        return service.preview_chunks(repository_id, request)

    @router.post(
        "/{repository_id}/chunks/index",
        response_model=ChunkIndexResponse,
        summary="Persist Java chunks for one source file",
        description="Generate AST-aware chunks through the secure reader and atomically replace this file's persisted chunk set.",
        responses={
            400: {"model": ErrorResponse, "description": "Unsupported source type or parse language."},
            403: _error_documentation("INVALID_SOURCE_PATH", "Source file path is not permitted."),
            404: {"model": ErrorResponse, "description": "Repository or source file not found."},
            413: _error_documentation("SOURCE_FILE_TOO_LARGE", "Source file exceeds the size limit."),
            422: {"model": ErrorResponse, "description": "Invalid UTF-8 or request parameters."},
            503: {
                "model": ErrorResponse,
                "description": "Cloud database or repository source is unavailable.",
                "content": {"application/json": {"examples": {
                    "not_configured": {"value": {"error": {
                        "code": "DATABASE_NOT_CONFIGURED",
                        "message": "Cloud database is not configured.",
                    }}},
                    "database_unavailable": {"value": {"error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Chunk persistence is unavailable.",
                    }}},
                }}},
            },
        },
    )
    def index_java_chunks(
        repository_id: UUID, request: ChunkPreviewRequest
    ) -> ChunkIndexResponse:
        return service.index_chunks(repository_id, request)

    @router.get(
        "/{repository_id}/chunks",
        response_model=PersistedChunksResponse,
        summary="List persisted code chunks",
        description="Return persisted chunks in file-path and source-offset order. Registration or indexing is required.",
        responses={
            404: _error_documentation("REPOSITORY_NOT_FOUND", "Repository not found."),
            503: {
                "model": ErrorResponse,
                "description": "Cloud database is not configured or is unavailable.",
                "content": {"application/json": {"examples": {
                    "not_configured": {"value": {"error": {
                        "code": "DATABASE_NOT_CONFIGURED",
                        "message": "Cloud database is not configured.",
                    }}},
                    "database_unavailable": {"value": {"error": {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "Chunk persistence is unavailable.",
                    }}},
                }}},
            },
        },
    )
    def list_persisted_chunks(
        repository_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> PersistedChunksResponse:
        return service.list_persisted_chunks(repository_id, limit, offset)

    return router


def _error_documentation(code: str, message: str) -> dict:
    return {
        "model": ErrorResponse,
        "description": message,
        "content": {
            "application/json": {
                "example": {"error": {"code": code, "message": message}}
            }
        },
    }
