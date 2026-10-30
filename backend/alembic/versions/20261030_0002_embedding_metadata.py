"""Store embedding fingerprints and status, without vectors.

Revision ID: 20261030_0002
Revises: 20261030_0001
"""

from alembic import op
import sqlalchemy as sa

revision = "20261030_0002"
down_revision = "20261030_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id", sa.String(length=64),
            sa.ForeignKey("code_chunks.chunk_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model_identifier", sa.String(length=200), primary_key=True),
        sa.Column("output_dimension", sa.Integer(), primary_key=True),
        sa.Column("input_version", sa.String(length=20), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_model_status", "chunk_embeddings",
        ["model_identifier", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embeddings_model_status", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
