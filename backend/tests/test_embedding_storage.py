"""Embedding metadata schema and SQL without a remote database."""

from unittest.mock import MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql

from app.db.models import Base, ChunkEmbeddingRow
from app.modules.vector_store.persistence import EmbeddingMetadata, EmbeddingMetadataStore


def test_metadata_schema_has_chunk_fk_and_vector_column() -> None:
    table = Base.metadata.tables["chunk_embeddings"]
    assert {
        "chunk_id", "model_identifier", "output_dimension", "input_version",
        "input_hash", "status", "error_code", "created_at", "updated_at",
        "embedding", "generated_at",
    } == set(table.c.keys())
    foreign_key = next(iter(ChunkEmbeddingRow.__table__.c.chunk_id.foreign_keys))
    assert foreign_key.target_fullname == "code_chunks.chunk_id"
    assert foreign_key.ondelete == "CASCADE"
    assert {column.name for column in table.primary_key.columns} == {
        "chunk_id", "model_identifier", "output_dimension", "input_hash"
    }
    assert str(table.c.embedding.type) == "VECTOR"


def test_embedding_metadata_upsert_is_idempotent_by_key() -> None:
    factory = MagicMock()
    session = factory.begin.return_value.__enter__.return_value
    record = EmbeddingMetadata(
        chunk_id="a" * 64, model_identifier="gemini-embedding-2",
        output_dimension=768, input_version="1.0.0",
        input_hash="b" * 64, status="COMPLETED", embedding=[0.1] * 768,
    )
    store = EmbeddingMetadataStore(factory)
    store.upsert_many([record])
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    assert "updated_at" in sql
    assert "embedding" in sql.lower()


def test_embedding_metadata_migration_is_next_revision() -> None:
    from pathlib import Path

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    revisions = ScriptDirectory.from_config(config)
    assert revisions.get_current_head() == "20261031_0005"
    assert revisions.get_revision("20261030_0002").down_revision == "20261030_0001"
