"""Deterministic, versioned text sent to the embedding provider."""

import hashlib

from app.modules.code_intelligence.chunking.schemas import JavaChunk

EMBEDDING_INPUT_VERSION = "1.0.0"


def build_document_input(chunk: JavaChunk) -> str:
    return (
        f"title: {chunk.qualified_name} | text: {chunk.contextual_header}\n"
        f"File: {chunk.file_path} | Kind: {chunk.chunk_type}\n"
        f"{chunk.content}"
    )


def embedding_input_hash(text: str) -> str:
    return hashlib.sha256(
        f"{EMBEDDING_INPUT_VERSION}\0{text}".encode("utf-8")
    ).hexdigest()
