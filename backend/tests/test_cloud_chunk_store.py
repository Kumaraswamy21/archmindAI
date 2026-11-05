"""Cloud database behavior without requiring a live database for unit tests."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.db.config import DatabaseSettings
from app.db.models import Base, CodeChunkRow
from app.db.session import create_database_engine
from app.main import create_app
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.repository_ingestion.registry import RepositoryRecord
from app.modules.repository_ingestion.persistence import ProjectRecord

DUMMY_URL = "postgresql+psycopg://placeholder:placeholder@db.example.invalid/archmind_test"


class FakeProjectStore:
    def __init__(self):
        self.records = {}

    def create(self, name, source_alias):
        record = ProjectRecord(uuid4(), name, source_alias, "REGISTERED", datetime.now(timezone.utc))
        self.records[record.repository_id] = record
        return record

    def get(self, repository_id):
        return self.records.get(repository_id)

    def list(self, limit, offset):
        records = list(self.records.values())
        return len(records), records[offset:offset + limit]


def _configured_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    approved = tmp_path / "approved"
    root = approved / "repository"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCHMIND_APPROVED_REPOSITORY_ROOTS", json.dumps([str(approved)]))
    monkeypatch.setenv("ARCHMIND_REPOSITORY_ALIASES", json.dumps({"sample": str(root)}))
    monkeypatch.setenv("DATABASE_URL", DUMMY_URL)
    app = create_app()
    app.state.repository_service.attach_store(FakeProjectStore())
    client = TestClient(app)
    response = client.post(
        "/api/v1/repositories", json={"name": "sample-repo", "source_alias": "sample"}
    )
    assert response.status_code == 201
    return client, root, response.json()["repository_id"]


def test_database_settings_require_remote_postgres_and_secure_ssl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        DatabaseSettings()
    for url in (
        "sqlite:///local.db",
        "postgresql+psycopg://u:p@localhost/db",
        "postgresql+psycopg://u:p@db.example.invalid/db?sslmode=disable",
    ):
        with pytest.raises(ValueError) as error:
            DatabaseSettings(database_url=url).sqlalchemy_url()
        assert "placeholder" not in str(error.value)
    with pytest.raises(ValidationError):
        DatabaseSettings(database_url=DUMMY_URL, database_ssl_mode="disable")
    settings = DatabaseSettings(database_url=DUMMY_URL)
    assert settings.database_ssl_mode == "require"
    assert settings.sqlalchemy_url().drivername == "postgresql+psycopg"
    assert "placeholder" not in repr(settings.database_url)


def test_invalid_url_error_does_not_expose_password() -> None:
    with pytest.raises(ValueError) as error:
        DatabaseSettings(
            database_url="postgresql+psycopg://user:VerySecret@localhost/db"
        ).sqlalchemy_url()
    assert "VerySecret" not in str(error.value)


def test_engine_uses_bounded_pool_and_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        captured["url"] = url
        return object()

    monkeypatch.setattr("app.db.session.create_engine", fake_create_engine)
    create_database_engine(DatabaseSettings(database_url=DUMMY_URL))
    assert captured["connect_args"]["sslmode"] == "require"
    assert captured["connect_args"]["connect_timeout"] == 5
    assert captured["pool_size"] == 5
    assert captured["max_overflow"] == 2
    assert captured["pool_timeout"] == 5
    assert captured["pool_pre_ping"] is True
    assert captured["echo"] is False


def test_schema_has_foreign_key_and_indexes() -> None:
    assert {"repositories", "code_chunks"} <= set(Base.metadata.tables)
    foreign_keys = CodeChunkRow.__table__.c.repository_id.foreign_keys
    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).target_fullname == "repositories.id"
    assert next(iter(foreign_keys)).ondelete == "RESTRICT"
    assert {index.name for index in CodeChunkRow.__table__.indexes} == {
        "ix_code_chunks_repository_file",
        "ix_code_chunks_repository_order",
        "ix_code_chunks_content_hash",
        "ix_code_chunks_search_vector",
        "ix_code_chunks_repository_symbol",
        "ix_code_chunks_repository_qualified",
        "ix_code_chunks_repository_parent",
    }
    assert "root_path" not in Base.metadata.tables["repositories"].c


def test_initial_migration_is_present() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "20261103_0007"
    assert script.get_revision("20261030_0001").down_revision is None


def test_persistence_uses_conflict_safe_inserts_and_content_hash() -> None:
    repository_id = uuid4()
    record = RepositoryRecord(
        repository_id=repository_id, name="sample", source_alias="sample",
        root_path=Path("/unused"), status="REGISTERED",
        registered_at=datetime.now(timezone.utc),
    )
    chunk = JavaChunk(
        chunk_id="a" * 64, repository_id=repository_id, file_path="A.java",
        language="java", chunk_type="method", parent_chunk_id=None,
        sequence=None, split_strategy=None, start_byte=0, end_byte=12,
        symbol_name="run", qualified_name="A.run", parent_symbol="A",
        package_name=None, imports=[], annotations=[], start_line=1, end_line=1,
        content="void run() {}", contextual_header="Class: A | Method: run",
    )
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    store = ChunkPersistence(factory)

    assert store.replace_file_chunks(record, "A.java", [chunk]) == 1
    statements = [call.args[0] for call in session.execute.call_args_list]
    sql = [str(statement.compile(dialect=postgresql.dialect())) for statement in statements]
    assert sum("ON CONFLICT" in statement for statement in sql) == 2
    assert any(statement.startswith("DELETE FROM code_chunks") for statement in sql)
    chunk_insert = statements[-1]
    assert chunk_insert._values[CodeChunkRow.__table__.c.content_hash].value == hashlib.sha256(
        chunk.content.encode("utf-8")
    ).hexdigest()
    assert "root_path" not in str(statements[0])


def test_index_and_pagination_with_in_memory_store_double(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, root, repository_id = _configured_client(tmp_path, monkeypatch)
    source = root / "A.java"
    source.write_text(
        "class A { void one() {} void two() {} void three() {} }",
        encoding="utf-8",
    )
    stored = {}
    def replace(_self, record, file_path, chunks):
        stored[record.repository_id] = list(chunks)
        return len(chunks)
    def list_chunks(_self, selected_id, limit, offset):
        chunks = stored.get(selected_id)
        return None if chunks is None else (len(chunks), chunks[offset : offset + limit])
    monkeypatch.setattr(ChunkPersistence, "replace_file_chunks", replace)
    monkeypatch.setattr(ChunkPersistence, "list_chunks", list_chunks)

    url = f"/api/v1/repositories/{repository_id}/chunks"
    request = {"file_path": "A.java"}
    first = client.post(f"{url}/index", json=request)
    second = client.post(f"{url}/index", json=request)
    assert first.status_code == second.status_code == 200
    assert first.json()["total_chunks"] == second.json()["total_chunks"] == 3
    page = client.get(url, params={"limit": 1, "offset": 1})
    assert page.status_code == 200
    assert page.json()["total_chunks"] == 3
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert [item["symbol_name"] for item in page.json()["chunks"]] == ["two"]
    restarted = TestClient(create_app())
    after_restart = restarted.get(url)
    assert after_restart.status_code == 200
    assert after_restart.json()["total_chunks"] == 3
    assert client.get(url, params={"limit": 101}).status_code == 422
    assert client.get(url, params={"offset": -1}).status_code == 422
    unknown = client.get(f"/api/v1/repositories/{uuid4()}/chunks")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


def test_missing_configuration_and_database_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.get(f"/api/v1/repositories/{uuid4()}/chunks")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"

    configured, root, repository_id = _configured_client(tmp_path, monkeypatch)
    (root / "A.java").write_text("class A {}", encoding="utf-8")
    failure = OperationalError("SELECT", {}, Exception("secret internal detail"))
    def fail(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(ChunkPersistence, "list_chunks", fail)
    monkeypatch.setattr(ChunkPersistence, "replace_file_chunks", fail)
    listed = configured.get(f"/api/v1/repositories/{repository_id}/chunks")
    indexed = configured.post(
        f"/api/v1/repositories/{repository_id}/chunks/index",
        json={"file_path": "A.java"},
    )
    for result in (listed, indexed):
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert "secret internal detail" not in result.text


def test_chunks_openapi_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _configured_client(tmp_path, monkeypatch)
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/repositories/{repository_id}/chunks"]
    assert path["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PersistedChunksResponse"
    }
    assert {"404", "422", "503"} <= set(path["get"]["responses"])
    assert {"limit", "offset", "repository_id"} == {
        item["name"] for item in path["get"]["parameters"]
    }
    assert "/api/v1/repositories/{repository_id}/chunks/index" in schema["paths"]
