"""Add stored PostgreSQL full-text vector and exact symbol indexes.

Revision ID: 20261030_0004
Revises: 20261030_0003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "20261030_0004"
down_revision = "20261030_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "code_chunks",
        sa.Column(
            "search_vector", TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, "
                "coalesce(symbol_name, '') || ' ' || coalesce(qualified_name, '') || ' ' || "
                "coalesce(contextual_header, '') || ' ' || coalesce(content, ''))",
                persisted=True,
            ),
        ),
    )
    op.create_index(
        "ix_code_chunks_search_vector", "code_chunks", ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_code_chunks_repository_symbol", "code_chunks",
        ["repository_id", "symbol_name"],
    )
    op.create_index(
        "ix_code_chunks_repository_qualified", "code_chunks",
        ["repository_id", "qualified_name"],
    )
    op.create_index(
        "ix_code_chunks_repository_parent", "code_chunks",
        ["repository_id", "parent_symbol"],
    )


def downgrade() -> None:
    op.drop_index("ix_code_chunks_repository_parent", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_qualified", table_name="code_chunks")
    op.drop_index("ix_code_chunks_repository_symbol", table_name="code_chunks")
    op.drop_index("ix_code_chunks_search_vector", table_name="code_chunks")
    op.drop_column("code_chunks", "search_vector")
