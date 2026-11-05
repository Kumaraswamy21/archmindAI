"""Bounded extraction of uploaded source archives."""

import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from app.errors import ApplicationError
from app.modules.repository_ingestion.discovery import EXCLUDED_DIRECTORIES, SOURCE_LANGUAGES

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000


def extract_sources(archive: Path, destination: Path, *, max_files: int,
                    max_file_size_bytes: int) -> int:
    """Extract only supported source files, rejecting unsafe archive entries."""
    count = 0
    total_size = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as package:
            if len(package.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise ApplicationError(413, "UPLOAD_LIMIT_EXCEEDED", "ZIP contains too many entries.")
            for entry in package.infolist():
                path = PurePosixPath(entry.filename)
                parts = path.parts
                mode = (entry.external_attr >> 16) & 0xFFFF
                if (not parts or path.is_absolute() or "\\" in entry.filename
                        or any(part in {"", ".", ".."} for part in parts)
                        or ":" in parts[0] or len(entry.filename) > 512
                        or (stat.S_IFMT(mode) != 0 and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
                    raise ApplicationError(422, "INVALID_ARCHIVE", "ZIP contains an unsafe path or file type.")
                if (entry.is_dir() or parts[-1].startswith("._")
                        or any(part in EXCLUDED_DIRECTORIES for part in parts[:-1])):
                    continue
                if path.suffix not in SOURCE_LANGUAGES:
                    continue
                key = path.as_posix()
                if key in seen:
                    raise ApplicationError(422, "INVALID_ARCHIVE", "ZIP contains duplicate source paths.")
                seen.add(key)
                count += 1
                total_size += entry.file_size
                if (count > max_files or entry.file_size > max_file_size_bytes
                        or total_size > MAX_EXTRACTED_BYTES):
                    raise ApplicationError(413, "UPLOAD_LIMIT_EXCEEDED", "ZIP exceeds source file limits.")
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if target.stat().st_size != entry.file_size:
                    raise ApplicationError(422, "INVALID_ARCHIVE", "ZIP contains an invalid source file.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, OSError) as exc:
        raise ApplicationError(422, "INVALID_ARCHIVE", "Choose a valid ZIP file containing source code.") from exc
    if count == 0:
        raise ApplicationError(422, "NO_SOURCE_FILES", "ZIP contains no supported .java, .py, or .ts source files.")
    return count


def temporary_upload_directory(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".upload-", dir=root))
