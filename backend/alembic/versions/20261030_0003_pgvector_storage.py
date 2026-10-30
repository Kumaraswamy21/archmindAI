"""Enable pgvector and persist model-scoped embedding vectors.

Revision ID: 20261030_0003
Revises: 20261030_0002
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

revision = "20261030_0003"
down_revision = "20261030_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is an explicit migration. Managed providers may require an
    # administrator to enable the extension before this statement can run.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("chunk_embeddings", sa.Column("embedding", VECTOR(), nullable=True))
    op.add_column(
        "chunk_embeddings",
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Phase 4.2 recorded successful requests but discarded their vectors.
    op.execute("UPDATE chunk_embeddings SET status = 'PENDING' WHERE status = 'COMPLETED'")
    op.drop_constraint("chunk_embeddings_pkey", "chunk_embeddings", type_="primary")
    op.create_primary_key(
        "chunk_embeddings_pkey", "chunk_embeddings",
        ["chunk_id", "model_identifier", "output_dimension", "input_hash"],
    )
    op.create_check_constraint(
        "ck_chunk_embeddings_supported_dimension", "chunk_embeddings",
        "output_dimension BETWEEN 128 AND 3072",
    )
    op.create_check_constraint(
        "ck_chunk_embeddings_vector_dimension", "chunk_embeddings",
        "embedding IS NULL OR vector_dims(embedding) = output_dimension",
    )
    op.create_check_constraint(
        "ck_chunk_embeddings_completed_vector", "chunk_embeddings",
        "status <> 'COMPLETED' OR embedding IS NOT NULL",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade needs a reviewed data-migration plan for historical vectors."
    )
