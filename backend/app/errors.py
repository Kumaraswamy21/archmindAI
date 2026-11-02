"""Application error response and handler."""

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApplicationError(Exception):
    """An expected application error with a public code and message."""

    def __init__(self, status_code: int, code: str, message: str,
                 details: list[dict[str, Any]] | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


async def handle_application_error(
    request: Request, exc: ApplicationError
) -> JSONResponse:
    response = ErrorResponse(error=ErrorDetail(
        code=exc.code, message=exc.message,
        request_id=getattr(request.state, "request_id", str(uuid4())),
        details=exc.details,
    ))
    return JSONResponse(status_code=exc.status_code, content=response.model_dump())


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"location": list(item.get("loc", ())),
                "message": item.get("msg", "Invalid value"),
                "type": item.get("type", "value_error")}
               for item in exc.errors()]
    return await handle_application_error(request, ApplicationError(
        422, "VALIDATION_ERROR", "Request validation failed.", details,
    ))


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return await handle_application_error(request, ApplicationError(
        exc.status_code, "HTTP_ERROR", str(exc.detail),
    ))


async def handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
    return await handle_application_error(request, ApplicationError(
        500, "INTERNAL_ERROR", "Internal server error.",
    ))
