"""PostgreSQL records for registered repositories and AST-aware chunks."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import VECTOR


class Base(DeclarativeBase):
    pass


class RepositoryRow(Base):
    __tablename__ = "repositories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    active_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    chunks: Mapped[list["CodeChunkRow"]] = relationship(back_populates="repository")


class CodeChunkRow(Base):
    __tablename__ = "code_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_name: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_symbol: Mapped[str | None] = mapped_column(Text)
    package_name: Mapped[str | None] = mapped_column(Text)
    imports: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    annotations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    end_byte: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64))
    sequence: Mapped[int | None] = mapped_column(Integer)
    split_strategy: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contextual_header: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple'::regconfig, "
            "coalesce(symbol_name, '') || ' ' || coalesce(qualified_name, '') || ' ' || "
            "coalesce(contextual_header, '') || ' ' || coalesce(content, ''))",
            persisted=True,
        ),
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    repository: Mapped[RepositoryRow] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_code_chunks_repository_file", "repository_id", "file_path"),
        Index("ix_code_chunks_repository_order", "repository_id", "file_path", "start_byte"),
        Index("ix_code_chunks_content_hash", "content_hash"),
        Index("ix_code_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_code_chunks_repository_symbol", "repository_id", "symbol_name"),
        Index("ix_code_chunks_repository_qualified", "repository_id", "qualified_name"),
        Index("ix_code_chunks_repository_parent", "repository_id", "parent_symbol"),
    )


class ChunkEmbeddingRow(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("code_chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_identifier: Mapped[str] = mapped_column(String(200), primary_key=True)
    output_dimension: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_version: Mapped[str] = mapped_column(String(20), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_chunk_embeddings_model_status", "model_identifier", "status"),
        CheckConstraint(
            "output_dimension BETWEEN 128 AND 3072",
            name="ck_chunk_embeddings_supported_dimension",
        ),
        CheckConstraint(
            "embedding IS NULL OR vector_dims(embedding) = output_dimension",
            name="ck_chunk_embeddings_vector_dimension",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR embedding IS NOT NULL",
            name="ck_chunk_embeddings_completed_vector",
        ),
    )


class DependencyRelationshipRow(Base):
    __tablename__ = "dependency_relationships"

    relationship_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False,
    )
    source_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    source_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    target_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        Index("ix_dependency_repository_source", "repository_id", "source_symbol"),
        Index("ix_dependency_repository_target", "repository_id", "target_symbol"),
        Index("ix_dependency_repository_type", "repository_id", "relationship_type"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="ck_dependency_line_range"),
        CheckConstraint(
            "relationship_type IN ('IMPORTS','EXTENDS','IMPLEMENTS','INJECTS','CALLS')",
            name="ck_dependency_relationship_type",
        ),
        CheckConstraint(
            "resolution_status IN ('RESOLVED','UNRESOLVED')",
            name="ck_dependency_resolution_status",
        ),
    )


class IndexJobRow(Base):
    __tablename__ = "index_jobs"

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    completed_stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(240))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_index_jobs_repository_created", "repository_id", "created_at"),
        Index("uq_index_jobs_active_repository", "repository_id", unique=True,
              postgresql_where=text("status IN ('QUEUED', 'RUNNING')")),
    )


class IndexStagedChunkRow(Base):
    __tablename__ = "index_staged_chunks"
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("index_jobs.job_id", ondelete="CASCADE"), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR())


class IndexStagedEdgeRow(Base):
    __tablename__ = "index_staged_edges"
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("index_jobs.job_id", ondelete="CASCADE"), primary_key=True)
    relationship_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"

    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    repository_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False,
    )
    workflow_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_workflow_runs_repository_updated", "repository_id", "updated_at"),
        Index("ix_workflow_runs_request", "request_id"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_workflow_runs_status",
        ),
        CheckConstraint(
            "workflow_type IN ('chat','ask','agents.analyze','index')",
            name="ck_workflow_runs_type",
        ),
    )
