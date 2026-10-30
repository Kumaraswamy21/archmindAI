"""Repository-scoped static Java dependency relationships.

Revision ID: 20261031_0005
Revises: 20261030_0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261031_0005"
down_revision = "20261030_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dependency_relationships",
        sa.Column("relationship_id", sa.String(64), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_symbol", sa.Text(), nullable=False),
        sa.Column("target_symbol", sa.Text(), nullable=False),
        sa.Column("relationship_type", sa.String(16), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("resolution_status", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.CheckConstraint("start_line >= 1 AND end_line >= start_line", name="ck_dependency_line_range"),
        sa.CheckConstraint(
            "relationship_type IN ('IMPORTS','EXTENDS','IMPLEMENTS','INJECTS','CALLS')",
            name="ck_dependency_relationship_type",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('RESOLVED','UNRESOLVED')",
            name="ck_dependency_resolution_status",
        ),
    )
    op.create_index("ix_dependency_repository_source", "dependency_relationships", ["repository_id", "source_symbol"])
    op.create_index("ix_dependency_repository_target", "dependency_relationships", ["repository_id", "target_symbol"])
    op.create_index("ix_dependency_repository_type", "dependency_relationships", ["repository_id", "relationship_type"])


def downgrade() -> None:
    op.drop_index("ix_dependency_repository_type", table_name="dependency_relationships")
    op.drop_index("ix_dependency_repository_target", table_name="dependency_relationships")
    op.drop_index("ix_dependency_repository_source", table_name="dependency_relationships")
    op.drop_table("dependency_relationships")
