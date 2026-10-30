"""Keyword retrieval orchestration and controlled database failures."""

from sqlalchemy.exc import SQLAlchemyError

from app.errors import ApplicationError
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.retrieval.persistence import KeywordSearchStore
from app.modules.retrieval.schemas import KeywordSearchRequest, KeywordSearchResponse


class KeywordSearchService:
    def __init__(
        self, repository_service: RepositoryService, store: KeywordSearchStore
    ) -> None:
        self._repository_service = repository_service
        self._store = store

    def search(self, request: KeywordSearchRequest) -> KeywordSearchResponse:
        try:
            results = self._store.search(request.repository_id, request.query, request.top_k)
        except SQLAlchemyError as exc:
            raise ApplicationError(
                503, "DATABASE_UNAVAILABLE", "Keyword search storage is unavailable."
            ) from exc
        if results is None:
            if self._repository_service.registered_record(request.repository_id) is None:
                raise ApplicationError(404, "REPOSITORY_NOT_FOUND", "Repository not found.")
            results = []
        return KeywordSearchResponse(repository_id=request.repository_id, results=results)
