"""Repository registration API."""

from uuid import UUID
import shutil

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from app.errors import ApplicationError, ErrorResponse
from app.modules.repository_ingestion.schemas import (
    RepositoryRegistrationRequest,
    RepositoryRegistrationResponse,
    RepositoryListResponse,
    ApprovedAliasesResponse,
    RepositoryFilesResponse,
    RepositoryFileContentResponse,
    RepositoryScanResponse,
)
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.repository_ingestion.uploads import MAX_ARCHIVE_BYTES, temporary_upload_directory


def create_router(service: RepositoryService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])

    def project_response(record) -> RepositoryRegistrationResponse:
        status = record.status if record.status == "REGISTERED" else "REGISTERED"
        return RepositoryRegistrationResponse(
            repository_id=record.repository_id, name=record.name,
            source_alias=record.source_alias, status=status,
            registered_at=record.registered_at,
        )

    @router.get("", response_model=RepositoryListResponse,
                responses={503: {"model": ErrorResponse}},
                summary="List registered projects with pagination")
    def list_repositories(limit: int = Query(50, ge=1, le=100),
                          offset: int = Query(0, ge=0)) -> RepositoryListResponse:
        total, records = service.list_projects(limit, offset)
        return RepositoryListResponse(total=total, limit=limit, offset=offset,
                                      repositories=[project_response(r) for r in records])

    @router.get("/approved-aliases", response_model=ApprovedAliasesResponse,
                summary="List server-approved repository aliases")
    def approved_aliases() -> ApprovedAliasesResponse:
        return ApprovedAliasesResponse(aliases=service.approved_aliases())

    @router.get("/{repository_id}", response_model=RepositoryRegistrationResponse,
                responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
                summary="Get persisted project metadata")
    def get_repository(repository_id: UUID) -> RepositoryRegistrationResponse:
        record = service.project(repository_id)
        if record is None:
            raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
        return project_response(record)

    @router.post(
        "",
        status_code=201,
        response_model=RepositoryRegistrationResponse,
        summary="Register an approved local repository",
        description="Register a repository through an administrator-configured source alias.",
        responses={
            404: {
                "model": ErrorResponse,
                "description": "Source alias not found.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "REPOSITORY_ALIAS_NOT_FOUND",
                                "message": "Repository source alias not found.",
                            }
                        }
                    }
                },
            },
            409: {
                "model": ErrorResponse,
                "description": "Repository already registered.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "REPOSITORY_ALREADY_REGISTERED",
                                "message": "Repository already registered.",
                            }
                        }
                    }
                },
            },
            503: {
                "model": ErrorResponse,
                "description": "Configured source unavailable.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "REPOSITORY_SOURCE_UNAVAILABLE",
                                "message": "Configured repository source is unavailable.",
                            }
                        }
                    }
                },
            },
        },
    )
    def register_repository(
        request: RepositoryRegistrationRequest,
    ) -> RepositoryRegistrationResponse:
        record = service.register(request)
        return project_response(record)

    @router.post("/upload", status_code=201, response_model=RepositoryRegistrationResponse,
                 responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
                 summary="Upload a ZIP repository")
    async def upload_repository(request: Request, name: str = Query(min_length=1, max_length=100)) -> RepositoryRegistrationResponse:
        if request.headers.get("content-type", "").split(";", 1)[0] not in {"application/zip", "application/octet-stream"}:
            raise ApplicationError(415, "UNSUPPORTED_UPLOAD", "Choose a ZIP file to upload.")
        if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_ARCHIVE_BYTES:
            raise ApplicationError(413, "UPLOAD_LIMIT_EXCEEDED", "ZIP must be 500 MB or smaller.")
        directory = temporary_upload_directory(service._configuration.upload_root)
        archive = directory / "source.zip"
        try:
            size = 0
            with archive.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise ApplicationError(413, "UPLOAD_LIMIT_EXCEEDED", "ZIP must be 500 MB or smaller.")
                    await run_in_threadpool(output.write, chunk)
            record = await run_in_threadpool(service.register_upload, name, archive)
            return project_response(record)
        finally:
            shutil.rmtree(directory)

    @router.get(
        "/{repository_id}/files",
        response_model=RepositoryFilesResponse,
        summary="Discover repository source files",
        description="List supported source files within a registered repository.",
        responses={
            404: {
                "model": ErrorResponse,
                "description": "Repository not found.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "REPOSITORY_NOT_FOUND",
                                "message": "Repository not found.",
                            }
                        }
                    }
                },
            },
            413: {
                "model": ErrorResponse,
                "description": "Discovery limit exceeded.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "DISCOVERY_LIMIT_EXCEEDED",
                                "message": "Repository discovery limit exceeded.",
                            }
                        }
                    }
                },
            },
            503: {
                "model": ErrorResponse,
                "description": "Repository unavailable.",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "REPOSITORY_UNAVAILABLE",
                                "message": "Registered repository is unavailable.",
                            }
                        }
                    }
                },
            },
        },
    )
    def list_repository_files(repository_id: UUID) -> RepositoryFilesResponse:
        return service.list_files(repository_id)

    @router.get(
        "/{repository_id}/files/content",
        response_model=RepositoryFileContentResponse,
        response_model_exclude_none=True,
        summary="Read a source file",
        description="Read a supported UTF-8 source file using a repository-relative path.",
        responses={
            400: _error_documentation(
                "UNSUPPORTED_SOURCE_FILE", "Unsupported source file type."
            ),
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
            416: _error_documentation(
                "SOURCE_LINE_RANGE_INVALID", "Requested source lines are unavailable."
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
                            "invalid_request": {
                                "value": {"detail": []}
                            },
                        }
                    }
                },
            },
            503: _error_documentation(
                "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable."
            ),
        },
    )
    def read_repository_file(
        repository_id: UUID,
        path: str = Query(
            ..., description="Repository-relative source file path.",
            examples=["src/main/java/OrderService.java"],
        ),
        start_line: int | None = Query(None, ge=1, description="First cited source line, inclusive."),
        end_line: int | None = Query(None, ge=1, description="Last cited source line, inclusive."),
    ) -> RepositoryFileContentResponse:
        return service.read_file(repository_id, path, start_line, end_line)

    @router.post(
        "/{repository_id}/scan",
        response_model=RepositoryScanResponse,
        summary="Scan repository source metadata",
        description="Synchronously summarize supported source files in a registered repository.",
        responses={
            404: _error_documentation("REPOSITORY_NOT_FOUND", "Repository not found."),
            413: _error_documentation(
                "DISCOVERY_LIMIT_EXCEEDED", "Repository discovery limit exceeded."
            ),
            503: _error_documentation(
                "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable."
            ),
        },
    )
    def scan_repository(repository_id: UUID) -> RepositoryScanResponse:
        return service.scan(repository_id)

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
