"""Application error response and handler."""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApplicationError(Exception):
    """An expected application error with a public code and message."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


async def handle_application_error(
    _request: Request, exc: ApplicationError
) -> JSONResponse:
    response = ErrorResponse(error=ErrorDetail(code=exc.code, message=exc.message))
    return JSONResponse(status_code=exc.status_code, content=response.model_dump())
