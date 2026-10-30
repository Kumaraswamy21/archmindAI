"""Read-only, snapshot-pinned comparison of existing retrieval services."""

import time
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.code_intelligence.chunking.schemas import JavaChunk
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.evaluation.dataset_loader import DatasetInvalid, DatasetLoader, DatasetNotFound
from app.modules.evaluation.metrics import calculate_metrics
from app.modules.evaluation.schemas import EvaluationRequest, EvaluationResponse, StrategyMetrics
from app.modules.evaluation.snapshot import snapshot_id
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.retrieval.hybrid_schemas import HybridSearchRequest
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.rerank_schemas import RerankRequest
from app.modules.retrieval.rerank_service import RerankSearchService
from app.modules.retrieval.schemas import KeywordSearchRequest
from app.modules.retrieval.service import KeywordSearchService
from app.modules.semantic_search.schemas import SemanticSearchRequest
from app.modules.semantic_search.service import SemanticSearchService


MAX_EVALUATION_CHUNKS = 1000


class RetrievalEvaluationService:
    def __init__(
        self,
        repository_service: RepositoryService,
        chunk_store: ChunkPersistence,
        semantic_service: SemanticSearchService,
        keyword_service: KeywordSearchService,
        hybrid_service: HybridSearchService,
        rerank_service: RerankSearchService,
        dataset_loader: DatasetLoader,
        embedding_model_identifier: str,
        reranker_version: str,
        *,
        clock=time.perf_counter,
    ) -> None:
        self._repository_service = repository_service
        self._chunk_store = chunk_store
        self._semantic_service = semantic_service
        self._keyword_service = keyword_service
        self._hybrid_service = hybrid_service
        self._rerank_service = rerank_service
        self._dataset_loader = dataset_loader
        self._embedding_model_identifier = embedding_model_identifier
        self._reranker_version = reranker_version
        self._clock = clock

    def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            dataset = self._dataset_loader.load(request.dataset)
        except DatasetNotFound as exc:
            raise ApplicationError(404, "DATASET_NOT_FOUND", "Evaluation dataset not found.") from exc
        except DatasetInvalid as exc:
            raise ApplicationError(503, "DATASET_INVALID", "Evaluation dataset is invalid.") from exc
        if not dataset.questions:
            raise ApplicationError(422, "EMPTY_DATASET", "Evaluation dataset has no questions.")
        chunks = self._load_chunks(request.repository_id)
        current_snapshot = snapshot_id(chunks)
        if current_snapshot != dataset.repository_snapshot_id:
            raise ApplicationError(409, "REPOSITORY_SNAPSHOT_MISMATCH", "Indexed repository does not match the evaluation snapshot.")
        symbols = {chunk.chunk_id: chunk.qualified_name for chunk in chunks}
        if any(
            not set(item.expected_symbols) <= set(symbols.values())
            for item in dataset.questions
        ):
            raise ApplicationError(503, "DATASET_INVALID", "Evaluation labels are absent from the pinned snapshot.")
        strategies = (
            ("semantic", self._semantic_service.search, SemanticSearchRequest),
            ("keyword", self._keyword_service.search, KeywordSearchRequest),
            ("hybrid", self._hybrid_service.search, HybridSearchRequest),
            ("hybrid_reranked", self._rerank_service.search, RerankRequest),
        )
        ranked: dict[str, list[list[str]]] = {name: [] for name, _, _ in strategies}
        latencies: dict[str, list[float]] = {name: [] for name, _, _ in strategies}
        for item in dataset.questions:
            for name, search, schema in strategies:
                started = self._clock()
                response = search(schema(
                    repository_id=request.repository_id,
                    query=item.question,
                    top_k=request.top_k,
                ))
                elapsed = (self._clock() - started) * 1000
                if response.repository_id != request.repository_id:
                    raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Retrieval crossed repository scope.")
                ids = [result.chunk_id for result in response.results]
                if any(chunk_id not in symbols for chunk_id in ids):
                    raise ApplicationError(503, "RETRIEVAL_SCOPE_MISMATCH", "Retrieval crossed repository scope.")
                ranked[name].append([symbols[chunk_id] for chunk_id in ids])
                latencies[name].append(elapsed)
        if snapshot_id(self._load_chunks(request.repository_id)) != current_snapshot:
            raise ApplicationError(409, "REPOSITORY_SNAPSHOT_CHANGED", "Indexed repository changed during evaluation.")
        expected = [set(item.expected_symbols) for item in dataset.questions]
        metrics = []
        for name, _, _ in strategies:
            values = calculate_metrics(expected, ranked[name], latencies[name])
            metrics.append(StrategyMetrics(
                strategy=name,
                recall_at_5=values.recall_at_5,
                mrr=values.mrr,
                average_latency_ms=values.average_latency_ms,
            ))
        return EvaluationResponse(
            repository_id=request.repository_id,
            dataset=dataset.dataset_id,
            dataset_version=dataset.version,
            repository_snapshot_id=current_snapshot,
            query_count=len(dataset.questions),
            strategies=metrics,
            evaluated_at=datetime.now(timezone.utc),
            embedding_model_identifier=self._embedding_model_identifier,
            reranker_version=self._reranker_version,
        )

    def _load_chunks(self, repository_id) -> list[JavaChunk]:
        chunks: list[JavaChunk] = []
        offset = 0
        while True:
            try:
                page = self._chunk_store.list_chunks(repository_id, 100, offset)
            except SQLAlchemyError as exc:
                raise ApplicationError(503, "DATABASE_UNAVAILABLE", "Evaluation storage is unavailable.") from exc
            if page is None:
                if self._repository_service.registered_record(repository_id) is None:
                    raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
                return []
            total, batch = page
            if total > MAX_EVALUATION_CHUNKS:
                raise ApplicationError(413, "EVALUATION_REPOSITORY_TOO_LARGE", "Evaluation snapshot exceeds the chunk limit.")
            chunks.extend(batch)
            offset += len(batch)
            if offset >= total or not batch:
                return chunks
