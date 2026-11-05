"""Workflow run records, index-job cancellation, and recoverable status.

Revision ID: 20261103_0007
Revises: 20261031_0006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261103_0007"
down_revision = "20261031_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_index_jobs_status", "index_jobs", type_="check")
    op.create_check_constraint(
        "ck_index_jobs_status", "index_jobs",
        "status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')",
    )
    op.add_column("index_jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
    op.create_table(
        "workflow_runs",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_workflow_runs_status",
        ),
        sa.CheckConstraint(
            "workflow_type IN ('chat','ask','agents.analyze','index')",
            name="ck_workflow_runs_type",
        ),
    )
    op.create_index("ix_workflow_runs_repository_updated", "workflow_runs",
                    ["repository_id", "updated_at"])
    op.create_index("ix_workflow_runs_request", "workflow_runs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_request", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_repository_updated", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_column("index_jobs", "cancel_requested_at")
    op.drop_constraint("ck_index_jobs_status", "index_jobs", type_="check")
    op.create_check_constraint(
        "ck_index_jobs_status", "index_jobs",
        "status IN ('QUEUED','RUNNING','COMPLETED','FAILED')",
    )
