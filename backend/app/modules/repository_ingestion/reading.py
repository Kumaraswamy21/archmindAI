"""Bounded, read-only access to supported source files."""

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from app.errors import ApplicationError
from app.modules.repository_ingestion.discovery import (
    EXCLUDED_DIRECTORIES,
    SOURCE_LANGUAGES,
    Language,
)


@dataclass(frozen=True)
class SourceFileContent:
    file_path: str
    language: Language
    line_count: int
    content: str


def read_source_file(
    root: Path, relative_path: str, *, max_file_size_bytes: int
) -> SourceFileContent:
    """Open path components relative to the root without following symlinks."""

    parts, language = _validate_path(relative_path)
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        try:
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ApplicationError(
                status_code=503,
                code="REPOSITORY_UNAVAILABLE",
                message="Registered repository is unavailable.",
            ) from exc
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd

        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise _invalid_path()
        if file_stat.st_size > max_file_size_bytes:
            raise _oversized_file()

        chunks = bytearray()
        while len(chunks) <= max_file_size_bytes:
            chunk = os.read(file_fd, min(65536, max_file_size_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > max_file_size_bytes:
            raise _oversized_file()

        try:
            content = chunks.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApplicationError(
                status_code=422,
                code="FILE_ENCODING_INVALID",
                message="Source file is not valid UTF-8.",
            ) from exc
        return SourceFileContent(
            file_path="/".join(parts),
            language=language,
            line_count=len(content.splitlines()),
            content=content,
        )
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise ApplicationError(
                status_code=404,
                code="SOURCE_FILE_NOT_FOUND",
                message="Source file not found.",
            ) from exc
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _invalid_path() from exc
        raise ApplicationError(
            status_code=503,
            code="REPOSITORY_UNAVAILABLE",
            message="Registered repository is unavailable.",
        ) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _validate_path(relative_path: str) -> tuple[list[str], Language]:
    parts = relative_path.split("/")
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or "\x00" in relative_path
        or PureWindowsPath(relative_path).drive
        or any(part in ("", ".", "..") for part in parts)
        or any(part in EXCLUDED_DIRECTORIES for part in parts[:-1])
    ):
        raise _invalid_path()

    language = SOURCE_LANGUAGES.get(Path(parts[-1]).suffix)
    if language is None:
        raise ApplicationError(
            status_code=400,
            code="UNSUPPORTED_SOURCE_FILE",
            message="Unsupported source file type.",
        )
    return parts, language


def _invalid_path() -> ApplicationError:
    return ApplicationError(
        status_code=403,
        code="INVALID_SOURCE_PATH",
        message="Source file path is not permitted.",
    )


def _oversized_file() -> ApplicationError:
    return ApplicationError(
        status_code=413,
        code="SOURCE_FILE_TOO_LARGE",
        message="Source file exceeds the size limit.",
    )
