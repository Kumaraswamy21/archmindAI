"""Administrator configuration and approved-root path checks."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.errors import ApplicationError


@dataclass(frozen=True)
class RepositoryConfiguration:
    aliases: dict[str, Path]
    approved_roots: tuple[Path, ...]
    max_files: int
    max_file_size_bytes: int
    upload_root: Path = Path(__file__).resolve().parents[3] / ".uploads"

    @classmethod
    def from_environment(cls) -> "RepositoryConfiguration":
        try:
            aliases = json.loads(os.environ.get("ARCHMIND_REPOSITORY_ALIASES", "{}"))
            roots = json.loads(os.environ.get("ARCHMIND_APPROVED_REPOSITORY_ROOTS", "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("Repository configuration must be valid JSON.") from exc

        if (
            not isinstance(aliases, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in aliases.items())
            or not isinstance(roots, list)
            or not all(isinstance(root, str) for root in roots)
        ):
            raise ValueError("Repository configuration has an invalid shape.")

        try:
            max_files = int(os.environ.get("ARCHMIND_DISCOVERY_MAX_FILES", "10000"))
            max_file_size_bytes = int(
                os.environ.get("ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES", "10485760")
            )
        except ValueError as exc:
            raise ValueError("Repository discovery limits must be positive integers.") from exc
        if max_files < 1 or max_file_size_bytes < 1:
            raise ValueError("Repository discovery limits must be positive integers.")

        return cls(
            aliases={alias: Path(path) for alias, path in aliases.items()},
            approved_roots=tuple(Path(root) for root in roots),
            max_files=max_files,
            max_file_size_bytes=max_file_size_bytes,
            upload_root=Path(os.environ.get("ARCHMIND_UPLOAD_ROOT", str(cls.upload_root))).resolve(),
        )


def resolve_approved_repository(path: Path, approved_roots: tuple[Path, ...]) -> Path:
    """Resolve symlinks and accept only existing directories under approved roots."""

    unavailable = ApplicationError(
        status_code=503,
        code="REPOSITORY_SOURCE_UNAVAILABLE",
        message="Configured repository source is unavailable.",
    )
    if not path.is_absolute() or not approved_roots:
        raise unavailable

    try:
        resolved_path = path.resolve(strict=True)
        if not resolved_path.is_dir():
            raise unavailable

        for root in approved_roots:
            if not root.is_absolute():
                continue
            try:
                resolved_root = root.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved_root.is_dir() and resolved_path.is_relative_to(resolved_root):
                return resolved_path
    except (OSError, RuntimeError) as exc:
        raise unavailable from exc

    raise unavailable
