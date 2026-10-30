"""Stable persisted-chunk snapshot identity independent of repository UUID."""

import hashlib
import json

from app.modules.code_intelligence.chunking.schemas import JavaChunk


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
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
