"""Durable indexing jobs and isolated staging before atomic snapshot promotion.

Revision ID: 20261031_0006
Revises: 20261031_0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import VECTOR

revision = "20261031_0006"
down_revision = "20261031_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repositories", sa.Column("active_snapshot_id", sa.String(64)))
    op.add_column("code_chunks", sa.Column("source_snapshot_id", sa.String(64)))
    op.add_column("dependency_relationships", sa.Column("source_snapshot_id", sa.String(64)))
    op.create_table(
        "index_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_stage", sa.String(32)),
        sa.Column("completed_stages", postgresql.JSONB(), nullable=False),
        sa.Column("source_snapshot_id", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("embedding_dimension", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','COMPLETED','FAILED')", name="ck_index_jobs_status"),
    )
    op.create_index("ix_index_jobs_repository_created", "index_jobs", ["repository_id", "created_at"])
    op.create_index("uq_index_jobs_active_repository", "index_jobs", ["repository_id"],
                    unique=True, postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"))
    op.create_table(
        "index_staged_chunks",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("index_jobs.job_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chunk_id", sa.String(64), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("embedding", VECTOR()),
    )
    op.create_table(
        "index_staged_edges",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("index_jobs.job_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relationship_id", sa.String(64), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("index_staged_edges")
    op.drop_table("index_staged_chunks")
    op.drop_index("uq_index_jobs_active_repository", table_name="index_jobs")
    op.drop_index("ix_index_jobs_repository_created", table_name="index_jobs")
    op.drop_table("index_jobs")
    op.drop_column("dependency_relationships", "source_snapshot_id")
    op.drop_column("code_chunks", "source_snapshot_id")
    op.drop_column("repositories", "active_snapshot_id")
