"""Vector persistence rules and index status without cloud access."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.errors import ApplicationError
from app.main import create_app
from app.modules.vector_store.config import EmbeddingIndexSettings
from app.modules.vector_store.input_builder import (
    EMBEDDING_INPUT_VERSION,
    build_document_input,
    embedding_input_hash,
)
from app.modules.vector_store.persistence import EmbeddingMetadata, EmbeddingMetadataStore
from app.modules.vector_store.schemas import IndexStatusResponse
from app.modules.vector_store.status import IndexStatusService


def _chunk(name, content="void run() {}"):
    from app.modules.code_intelligence.chunking.schemas import JavaChunk

    return JavaChunk(
        chunk_id=name.ljust(64, "0"), repository_id=uuid4(),
        file_path="A.java", language="java", chunk_type="method",
        parent_chunk_id=None, sequence=None, split_strategy=None,
        start_byte=0, end_byte=len(content.encode()),
        symbol_name=name, qualified_name=f"A.{name}", parent_symbol="A",
        package_name=None, imports=[], annotations=[], start_line=1, end_line=1,
        content=content, contextual_header=f"Class: A | Method: {name}",
    )


class ChunkStore:
    def __init__(self, chunks):
        self.chunks = chunks
        self.found = True

    def list_chunks(self, _id, limit, offset):
        return (len(self.chunks), self.chunks[offset : offset + limit]) if self.found else None


class MetadataStore:
    def __init__(self):
        self.records = {}

    def get_many(self, hashes, model, dimension):
        return {
            chunk_id: self.records[(chunk_id, model, dimension, input_hash)]
            for chunk_id, input_hash in hashes.items()
            if (chunk_id, model, dimension, input_hash) in self.records
        }


def _record(chunk, *, status="COMPLETED", model="gemini-embedding-2", dimension=128):
    input_hash = embedding_input_hash(build_document_input(chunk))
    return EmbeddingMetadata(
        chunk_id=chunk.chunk_id, model_identifier=model, output_dimension=dimension,
        input_version=EMBEDDING_INPUT_VERSION, input_hash=input_hash,
        status=status, embedding=[0.1] * dimension if status == "COMPLETED" else None,
    )


def _status_service(chunks):
    store = ChunkStore(chunks)
    metadata = MetadataStore()
    repository = SimpleNamespace(registered_record=lambda _id: object())
    settings = EmbeddingIndexSettings(embedding_dimension=128)
    return IndexStatusService(repository, store, metadata, settings), store, metadata


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([], (0, 0, 0, 0, "EMPTY")),
        (["none"], (1, 0, 1, 0, "PENDING")),
        (["complete"], (1, 1, 0, 0, "READY")),
        (["failed"], (1, 0, 0, 1, "FAILED")),
        (["complete", "none", "failed"], (3, 1, 1, 1, "PARTIAL")),
    ],
)
def test_status_counts_actual_current_vectors(states, expected) -> None:
    chunks = [_chunk(f"m{index}") for index in range(len(states))]
    service, _, metadata = _status_service(chunks)
    for item, state in zip(chunks, states):
        if state != "none":
            record = _record(item, status="COMPLETED" if state == "complete" else "FAILED")
            metadata.records[
                (item.chunk_id, record.model_identifier, record.output_dimension, record.input_hash)
            ] = record
    result = service.get_status(uuid4())
    assert (
        result.total_chunks, result.embedded_chunks, result.pending_chunks,
        result.failed_chunks, result.index_status,
    ) == expected


def test_status_ignores_other_model_dimension_and_old_input_hash() -> None:
    item = _chunk("run")
    service, _, metadata = _status_service([item])
    for record in (
        _record(item, model="old-model"),
        _record(item, dimension=256),
        _record(item.model_copy(update={"content": "old source"})),
    ):
        metadata.records[
            (item.chunk_id, record.model_identifier, record.output_dimension, record.input_hash)
        ] = record
    result = service.get_status(uuid4())
    assert result.index_status == "PENDING"
    assert result.embedded_chunks == 0
    assert result.pending_chunks == 1


def test_missing_repo_and_db_failure_are_controlled() -> None:
    service, chunks, _ = _status_service([])
    chunks.found = False
    service._repository_service.registered_record = lambda _id: None
    with pytest.raises(ApplicationError) as error:
        service.get_status(uuid4())
    assert error.value.status_code == 404
    chunks.list_chunks = lambda *_args: (_ for _ in ()).throw(
        OperationalError("SELECT", {}, Exception("private connection detail"))
    )
    with pytest.raises(ApplicationError) as error:
        service.get_status(uuid4())
    assert error.value.status_code == 503
    assert "private connection detail" not in error.value.message


def test_vector_upsert_guards_dimension_and_preserves_completed_row() -> None:
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    store = EmbeddingMetadataStore(factory)
    record = EmbeddingMetadata(
        chunk_id="a" * 64, model_identifier="gemini-embedding-2",
        output_dimension=128, input_version="1.0.0", input_hash="b" * 64,
        status="COMPLETED", embedding=[0.1] * 128,
    )
    store.upsert_many([record])
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    assert "input_hash" in sql
    assert "WHERE" in sql
    assert "COMPLETED" not in sql  # bound parameter, never interpolated into SQL
    with pytest.raises(ValueError):
        store.upsert_many([record.__class__(**{**record.__dict__, "embedding": [0.1] * 127})])
    with pytest.raises(ValueError):
        store.upsert_many([record.__class__(**{**record.__dict__, "embedding": [0.0] * 128})])


def test_index_status_openapi_and_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    repository_id = uuid4()
    unconfigured = TestClient(create_app())
    response = unconfigured.get(f"/api/v1/repositories/{repository_id}/index/status")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://placeholder:placeholder@db.example.invalid/archmind_test",
    )
    monkeypatch.setattr(
        IndexStatusService, "get_status",
        lambda _self, _id: IndexStatusResponse(
            repository_id=repository_id, total_chunks=2,
            embedded_chunks=2, pending_chunks=0, failed_chunks=0,
            index_status="READY",
        ),
    )
    client = TestClient(create_app())
    result = client.get(f"/api/v1/repositories/{repository_id}/index/status")
    assert result.status_code == 200
    assert result.json() == {
        "repository_id": str(repository_id), "total_chunks": 2,
        "embedded_chunks": 2, "pending_chunks": 0,
        "failed_chunks": 0, "index_status": "READY",
    }
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/repositories/{repository_id}/index/status"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IndexStatusResponse"
    }
    assert {"404", "503"} <= set(operation["responses"])
    assert "embedding" not in str(schema["components"]["schemas"]["IndexStatusResponse"]).lower()
