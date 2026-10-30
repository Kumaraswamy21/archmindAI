"""Create repository metadata and AST-aware code chunk tables.

Revision ID: 20261030_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261030_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("source_alias", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_table(
        "code_chunks",
        sa.Column("chunk_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "repository_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("chunk_type", sa.String(length=32), nullable=False),
        sa.Column("symbol_name", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("parent_symbol", sa.Text()),
        sa.Column("package_name", sa.Text()),
        sa.Column("imports", postgresql.JSONB(), nullable=False),
        sa.Column("annotations", postgresql.JSONB(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("start_byte", sa.Integer(), nullable=False),
        sa.Column("end_byte", sa.Integer(), nullable=False),
        sa.Column("parent_chunk_id", sa.String(length=64)),
        sa.Column("sequence", sa.Integer()),
        sa.Column("split_strategy", sa.String(length=16)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contextual_header", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_code_chunks_repository_file", "code_chunks",
        ["repository_id", "file_path"],
    )
    op.create_index(
        "ix_code_chunks_repository_order", "code_chunks",
        ["repository_id", "file_path", "start_byte"],
    )
    op.create_index(
        "ix_code_chunks_content_hash", "code_chunks", ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_code_chunks_content_hash", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_order", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_file", table_name="code_chunks")
    op.drop_table("code_chunks")
    op.drop_table("repositories")
