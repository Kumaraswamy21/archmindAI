"""Reusable, read-only source-file traversal."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.errors import ApplicationError

Language = Literal["java", "python", "typescript"]

SOURCE_LANGUAGES: dict[str, Language] = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
}
EXCLUDED_DIRECTORIES = frozenset(
    {".git", "node_modules", "target", "build", "dist", "__pycache__", ".venv", "__MACOSX"}
)


@dataclass(frozen=True)
class DiscoveredFile:
    path: str
    language: Language
    size_bytes: int


def discover_source_files(
    root: Path, *, max_files: int, max_file_size_bytes: int
) -> list[DiscoveredFile]:
    """Find supported files without following directory or file symlinks."""

    files: list[DiscoveredFile] = []

    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for directory, dirnames, filenames, directory_fd in os.fwalk(
                ".", dir_fd=root_fd, follow_symlinks=False, onerror=_raise_walk_error
            ):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if name not in EXCLUDED_DIRECTORIES
                    and not stat.S_ISLNK(
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
                    )
                ]
                relative_parts = Path(directory).parts
                for name in filenames:
                    if name.startswith("._"):
                        continue
                    language = SOURCE_LANGUAGES.get(Path(name).suffix)
                    if language is None:
                        continue
                    file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not stat.S_ISREG(file_stat.st_mode):
                        continue
                    if file_stat.st_size > max_file_size_bytes or len(files) >= max_files:
                        raise ApplicationError(
                            status_code=413,
                            code="DISCOVERY_LIMIT_EXCEEDED",
                            message="Repository discovery limit exceeded.",
                        )
                    files.append(
                        DiscoveredFile(
                            path="/".join((*relative_parts, name)),
                            language=language,
                            size_bytes=file_stat.st_size,
                        )
                    )
        finally:
            os.close(root_fd)
    except OSError as exc:
        raise ApplicationError(
            status_code=503,
            code="REPOSITORY_UNAVAILABLE",
            message="Registered repository is unavailable.",
        ) from exc

    return sorted(files, key=lambda file: file.path)


def _raise_walk_error(error: OSError) -> None:
    raise error
