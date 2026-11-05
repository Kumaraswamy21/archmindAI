"""Stable persisted-chunk snapshot identity independent of repository UUID."""

import hashlib
import json

from sqlalchemy.exc import SQLAlchemyError

from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.errors import ApplicationError


MAX_EVALUATION_CHUNKS = 1000


def load_indexed_chunks(
    repository_service, chunk_store, repository_id
) -> list[JavaChunk]:
    chunks: list[JavaChunk] = []
    offset = 0
    while True:
        try:
            page = chunk_store.list_chunks(repository_id, 100, offset)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Evaluation storage is unavailable."
            ) from exc
        if page is None:
            if repository_service.registered_record(repository_id) is None:
                raise ApplicationError(
                    404, "REPOSITORY_NOT_FOUND", "Repository not found."
                )
            return []
        total, batch = page
        if total > MAX_EVALUATION_CHUNKS:
            raise ApplicationError(
                413,
                "EVALUATION_REPOSITORY_TOO_LARGE",
                "Evaluation snapshot exceeds the chunk limit.",
            )
        chunks.extend(batch)
        offset += len(batch)
        if offset >= total or not batch:
            return chunks


def snapshot_id(chunks: list[JavaChunk]) -> str:
    entries = [
        [
            chunk.file_path,
            chunk.qualified_name,
            chunk.chunk_type,
            chunk.start_byte,
            chunk.end_byte,
            hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
        ]
        for chunk in chunks
    ]
    entries.sort()
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
