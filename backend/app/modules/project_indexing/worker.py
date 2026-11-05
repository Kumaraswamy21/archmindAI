"""Separate durable PostgreSQL worker for repository indexing jobs."""

import hashlib
import time

from sqlalchemy.exc import SQLAlchemyError

from app.core.retry import retry_transient
from app.db.config import DatabaseSettings
from app.db.session import create_database_engine, create_session_factory
from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.java_chunker import JavaAstChunker
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.symbol_extractor import JavaSymbolExtractor
from app.modules.dependency_intelligence.extractor import JavaDependencyExtractor, JavaSourceUnit
from app.modules.project_indexing.persistence import RESUMABLE_STAGES, ClaimedJob, IndexJobStore
from app.modules.repository_ingestion.persistence import ProjectStore
from app.modules.repository_ingestion.registry import RepositoryRegistry
from app.modules.repository_ingestion.security import RepositoryConfiguration
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.vector_store.config import EmbeddingSettings
from app.modules.vector_store.embeddings.base import EmbeddingProvider, EmbeddingProviderError
from app.modules.vector_store.embeddings.gemini import GeminiEmbeddingProvider
from app.modules.vector_store.input_builder import build_document_input, embedding_input_hash
from app.modules.vector_store.service import EmbeddingService
from app.modules.workflow_reliability.store import WorkflowStore


class IndexWorker:
    def __init__(self, jobs: IndexJobStore, repositories: RepositoryService,
                 provider: EmbeddingProvider, settings: EmbeddingSettings,
                 workflows: WorkflowStore | None = None) -> None:
        self.jobs = jobs
        self.repositories = repositories
        self.provider = provider
        self.settings = settings
        self.workflows = workflows
        self.parser = JavaParserAdapter()
        self.symbols = JavaSymbolExtractor()
        self.chunker = JavaAstChunker()
        self.dependencies = JavaDependencyExtractor()

    def process_one(self) -> bool:
        job = self.jobs.claim()
        if job is None:
            return False
        self._sync(job)
        try:
            self._execute(job)
        except ApplicationError as exc:
            self._record_failure(job, exc.code, exc.message)
        except EmbeddingProviderError as exc:
            self._record_failure(job, exc.code, "Embedding generation failed.")
        except SQLAlchemyError:
            self._record_failure(job, "DATABASE_UNAVAILABLE", "Indexing storage is unavailable.")
        except Exception:
            self._record_failure(job, "INDEXING_FAILED", "Indexing failed.")
        finally:
            self._sync(job)
        return True

    def _sync(self, job: ClaimedJob) -> None:
        if self.workflows is None:
            return
        try:
            current = self.jobs.get(job.repository_id, job.job_id)
            if current is not None:
                self.workflows.sync_index(current)
        except (ApplicationError, SQLAlchemyError):
            # The index job remains authoritative and can be reconciled by its API.
            pass

    def _record_failure(self, job: ClaimedJob, code: str, message: str) -> None:
        try:
            self.jobs.fail(job, code, message)
        except RuntimeError:
            # A replacement worker owns the expired lease; it controls job state.
            pass

    def _ensure_active(self, job: ClaimedJob) -> None:
        if self.jobs.is_cancel_requested(job):
            raise ApplicationError(409, "INDEX_JOB_CANCELLED", "Indexing cancelled at user request.")

    def _execute(self, job: ClaimedJob) -> None:
        self._ensure_active(job)
        if self.repositories.registered_record(job.repository_id) is None:
            raise ApplicationError(503, "REPOSITORY_UNAVAILABLE", "Registered repository is unavailable.")
        discovered = self.repositories.list_files(job.repository_id)
        paths = sorted(item.path for item in discovered.files if item.language == "java")
        if job.current_stage in RESUMABLE_STAGES and job.source_snapshot_id:
            self._resume(job, paths)
            return
        self.jobs.advance(job, "DISCOVERING", "PARSING")

        manifest: dict[str, str] = {}
        digest = hashlib.sha256(b"archmind-java-snapshot-v1\0")
        for path in paths:
            source = self.repositories.read_file(job.repository_id, path)
            syntax = self.parser.parse_tree(source.content)
            if syntax.tree.root_node.has_error:
                raise ApplicationError(422, "JAVA_SYNTAX_INVALID", "A Java source file contains syntax errors.")
            content_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
            manifest[path] = content_hash
            digest.update(path.encode("utf-8") + b"\0" + content_hash.encode("ascii") + b"\0")
            self._ensure_active(job)
            self.jobs.heartbeat(job)
        snapshot_id = digest.hexdigest()
        self.jobs.advance(job, "PARSING", "CHUNKING", snapshot_id)

        chunk_count = 0
        for path in paths:
            source = self.repositories.read_file(job.repository_id, path)
            if hashlib.sha256(source.content.encode("utf-8")).hexdigest() != manifest[path]:
                raise ApplicationError(409, "SOURCE_CHANGED", "Repository source changed during indexing.")
            syntax = self.parser.parse_tree(source.content)
            extracted = self.symbols.extract(syntax)
            chunks = self.chunker.chunk(job.repository_id, path, syntax, extracted)
            for start in range(0, len(chunks), 50):
                batch = [chunk.model_dump(mode="json") for chunk in chunks[start:start + 50]]
                self.jobs.stage_chunks(job, batch)
            chunk_count += len(chunks)
            self._ensure_active(job)
            self.jobs.heartbeat(job)
        self.jobs.advance(job, "CHUNKING", "PERSISTING")
        staged = self.jobs.staged_chunks(job)
        if len(staged) != chunk_count:
            raise ApplicationError(503, "STAGING_INCOMPLETE", "Chunk staging is incomplete.")
        self.jobs.advance(job, "PERSISTING", "EMBEDDING")

        self._embed_missing(job, staged)
        self.jobs.advance(job, "EMBEDDING", "VECTOR_INDEXING")
        self.jobs.validate_vectors(job, self.settings.embedding_dimension)
        self.jobs.advance(job, "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION")
        self._extract_and_promote(job, paths, manifest, snapshot_id)

    def _resume(self, job: ClaimedJob, paths: list[str]) -> None:
        snapshot_id, manifest = self._hash_snapshot(job, paths)
        if snapshot_id != job.source_snapshot_id:
            raise ApplicationError(409, "SOURCE_CHANGED", "Repository source changed during indexing.")
        staged = self.jobs.staged_chunks(job)
        if job.current_stage == "EMBEDDING":
            self._embed_missing(job, staged)
            self.jobs.advance(job, "EMBEDDING", "VECTOR_INDEXING")
        if job.current_stage in {"EMBEDDING", "VECTOR_INDEXING"}:
            self.jobs.validate_vectors(job, self.settings.embedding_dimension)
            self.jobs.advance(job, "VECTOR_INDEXING", "DEPENDENCY_EXTRACTION")
        self._extract_and_promote(job, paths, manifest, snapshot_id)

    def _hash_snapshot(self, job: ClaimedJob, paths: list[str]) -> tuple[str, dict[str, str]]:
        manifest: dict[str, str] = {}
        digest = hashlib.sha256(b"archmind-java-snapshot-v1\0")
        for path in paths:
            source = self.repositories.read_file(job.repository_id, path)
            content_hash = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
            manifest[path] = content_hash
            digest.update(path.encode("utf-8") + b"\0" + content_hash.encode("ascii") + b"\0")
            self._ensure_active(job)
            self.jobs.heartbeat(job)
        return digest.hexdigest(), manifest

    def _embed_missing(self, job: ClaimedJob, staged: list[tuple[str, dict, list[float] | None]]) -> None:
        size = self.settings.embedding_batch_size
        for start in range(0, len(staged), size):
            self._ensure_active(job)
            batch = [item for item in staged[start:start + size] if item[2] is None]
            if not batch:
                continue
            inputs = [build_document_input(JavaChunk.model_validate(payload)) for _, payload, _ in batch]
            vectors = self._embed(inputs)
            EmbeddingService._validate_vectors(vectors, len(batch), self.settings.embedding_dimension)
            for (chunk_id, _, _), input_text, vector in zip(batch, inputs, vectors, strict=True):
                self.jobs.set_embedding(job, chunk_id, embedding_input_hash(input_text), vector)
            self.jobs.heartbeat(job)

    def _extract_and_promote(
        self, job: ClaimedJob, paths: list[str], manifest: dict[str, str], snapshot_id: str,
    ) -> None:
        units: list[JavaSourceUnit] = []
        current_paths = sorted(item.path for item in self.repositories.list_files(job.repository_id).files
                               if item.language == "java")
        if current_paths != paths:
            raise ApplicationError(409, "SOURCE_CHANGED", "Repository source changed during indexing.")
        for path in paths:
            source = self.repositories.read_file(job.repository_id, path)
            if hashlib.sha256(source.content.encode("utf-8")).hexdigest() != manifest[path]:
                raise ApplicationError(409, "SOURCE_CHANGED", "Repository source changed during indexing.")
            syntax = self.parser.parse_tree(source.content)
            if syntax.tree.root_node.has_error:
                raise ApplicationError(422, "JAVA_SYNTAX_INVALID", "A Java source file contains syntax errors.")
            units.append(JavaSourceUnit(file_path=path, syntax=syntax,
                                        extracted=self.symbols.extract(syntax)))
            self._ensure_active(job)
            self.jobs.heartbeat(job)
        edges = self.dependencies.extract(job.repository_id, units)
        self.jobs.stage_edges(job, [edge.model_dump(mode="json") for edge in edges])
        self.jobs.promote(job, snapshot_id, self.settings.embedding_model,
                          self.settings.embedding_dimension)

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        return retry_transient(
            lambda: self.provider.embed_documents(inputs),
            max_attempts=self.settings.embedding_max_retries + 1,
            backoff_seconds=self.settings.embedding_retry_backoff_seconds,
        )


def main() -> None:
    settings = EmbeddingSettings()
    engine = create_database_engine(DatabaseSettings(), register_vectors=True)
    sessions = create_session_factory(engine)
    repositories = RepositoryService(
        RepositoryConfiguration.from_environment(), RepositoryRegistry(), ProjectStore(sessions),
    )
    provider = GeminiEmbeddingProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        model_identifier=settings.embedding_model,
        output_dimension=settings.embedding_dimension,
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )
    worker = IndexWorker(IndexJobStore(sessions), repositories, provider, settings,
                         WorkflowStore(sessions))
    try:
        while True:
            if not worker.process_one():
                time.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
