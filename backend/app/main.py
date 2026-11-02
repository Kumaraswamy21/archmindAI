"""FastAPI application entry point."""

import os
import json
from importlib.util import find_spec
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.health import router as health_router
from app.api.questions import router as questions_router
from app.errors import ApplicationError, handle_application_error, handle_validation_error, handle_http_error, handle_unexpected_error
from app.db.config import DatabaseSettings
from app.db.session import create_database_engine, create_session_factory
from app.modules.code_intelligence.java_parser import JavaParserAdapter
from app.modules.code_intelligence.persistence import ChunkPersistence
from app.modules.code_intelligence.router import create_router as create_parse_router
from app.modules.code_intelligence.service import CodeIntelligenceService
from app.modules.repository_ingestion.registry import RepositoryRegistry
from app.modules.repository_ingestion.persistence import ProjectStore
from app.modules.project_indexing.persistence import IndexJobStore
from app.modules.project_indexing.router import create_router as create_index_job_router
from app.modules.repository_ingestion.router import create_router as create_repository_router
from app.modules.repository_ingestion.security import RepositoryConfiguration
from app.modules.repository_ingestion.service import RepositoryService
from app.modules.retrieval.persistence import KeywordSearchStore
from app.modules.retrieval.router import create_router as create_keyword_router
from app.modules.retrieval.service import KeywordSearchService
from app.modules.retrieval.hybrid_config import HybridSearchSettings
from app.modules.retrieval.hybrid_persistence import HybridChunkStore
from app.modules.retrieval.hybrid_router import create_router as create_hybrid_router
from app.modules.retrieval.hybrid_service import HybridSearchService
from app.modules.retrieval.rerank_persistence import RerankMetadataStore
from app.modules.retrieval.rerank_router import create_router as create_rerank_router
from app.modules.retrieval.rerank_service import RerankSearchService
from app.modules.retrieval.reranker import MetadataHeuristicV1
from app.modules.rag.config import RagSettings
from app.modules.rag.llm_provider import GeminiAnswerProvider
from app.modules.rag.persistence import RagEvidenceStore
from app.modules.rag.router import create_router as create_rag_router
from app.modules.rag.service import RagService
from app.modules.agentic.router import create_router as create_agentic_router
from app.modules.chat.router import create_router as create_chat_router
from app.modules.chat.service import ChatService
from app.modules.chat.streaming import StreamEvent
from app.modules.agentic.workflow import AgenticWorkflow
from app.modules.agentic.classifier import IntentClassifier
from app.modules.agentic.code_analysis import CodeAnalysisAgent
from app.modules.agentic.dependency_analysis import DependencyAnalysisAgent
from app.modules.agentic.architecture_analysis import ArchitectureAnalysisAgent
from app.modules.agentic.analysis_common import PromptedAnalyzer
from app.prompts.loader import PromptLoader
from app.modules.evaluation.dataset_loader import DatasetLoader
from app.modules.evaluation.router import create_router as create_evaluation_router
from app.modules.evaluation.service import RetrievalEvaluationService
from app.modules.dependency_intelligence.persistence import DependencyPersistence
from app.modules.dependency_intelligence.router import create_router as create_dependency_router
from app.modules.dependency_intelligence.service import DependencyIntelligenceService
from app.modules.semantic_search.persistence import SemanticSearchStore
from app.modules.semantic_search.router import create_router as create_search_router
from app.modules.semantic_search.service import SemanticSearchService
from app.modules.vector_store.config import EmbeddingIndexSettings, EmbeddingSettings
from app.modules.vector_store.embeddings.gemini import GeminiEmbeddingProvider
from app.modules.vector_store.persistence import EmbeddingMetadataStore
from app.modules.vector_store.router import create_router as create_embedding_router
from app.modules.vector_store.service import EmbeddingService
from app.modules.vector_store.status import IndexStatusService


def create_app() -> FastAPI:
    app = FastAPI(
        title="ArchMind AI API",
        version="0.1.0",
        description="Read-only repository intelligence and architecture analysis platform",
    )

    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    origins = json.loads(os.environ.get("ARCHMIND_FRONTEND_ORIGINS", "[]"))
    if not isinstance(origins, list) or any(not isinstance(origin, str) for origin in origins):
        raise ValueError("ARCHMIND_FRONTEND_ORIGINS must be a JSON list of origins.")
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.path or parsed.query or parsed.fragment or "*" in origin):
            raise ValueError("ARCHMIND_FRONTEND_ORIGINS contains an invalid origin.")
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", "X-Request-ID"])

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    app.include_router(health_router)
    app.include_router(questions_router)

    service = RepositoryService(
        RepositoryConfiguration.from_environment(), RepositoryRegistry()
    )
    app.state.repository_service = service
    app.include_router(create_repository_router(service))
    persistence = None
    embedding_service = None
    index_status_service = None
    search_service = None
    keyword_service = None
    hybrid_service = None
    rerank_service = None
    rag_service = None
    agentic_service = None
    evaluation_service = None
    dependency_service = None
    index_job_store = None
    if os.environ.get("DATABASE_URL"):
        settings = DatabaseSettings()
        engine = create_database_engine(settings, register_vectors=True)
        app.state.db_engine = engine
        session_factory = create_session_factory(engine)
        service.attach_store(ProjectStore(session_factory))
        index_job_store = IndexJobStore(session_factory)
        app.state.index_job_store = index_job_store
        persistence = ChunkPersistence(session_factory)
        dependency_service = DependencyIntelligenceService(
            service, DependencyPersistence(session_factory)
        )
        keyword_service = KeywordSearchService(
            service, KeywordSearchStore(session_factory)
        )
        metadata_store = EmbeddingMetadataStore(session_factory)
        index_status_service = IndexStatusService(
            service, persistence, metadata_store, EmbeddingIndexSettings()
        )
        if os.environ.get("GEMINI_API_KEY", "").strip():
            embedding_settings = EmbeddingSettings()
            provider = GeminiEmbeddingProvider(
                api_key=embedding_settings.gemini_api_key.get_secret_value(),
                model_identifier=embedding_settings.embedding_model,
                output_dimension=embedding_settings.embedding_dimension,
                timeout_seconds=embedding_settings.embedding_request_timeout_seconds,
            )
            embedding_service = EmbeddingService(
                service, persistence, metadata_store,
                provider, embedding_settings,
            )
            search_service = SemanticSearchService(
                service, persistence, SemanticSearchStore(session_factory), provider,
                embedding_settings,
            )
            hybrid_service = HybridSearchService(
                search_service, keyword_service, HybridChunkStore(session_factory),
                HybridSearchSettings(),
            )
            rerank_service = RerankSearchService(
                hybrid_service, RerankMetadataStore(session_factory),
                MetadataHeuristicV1(), HybridSearchSettings(),
            )
            rag_settings = RagSettings()
            answer_provider = GeminiAnswerProvider(
                api_key=rag_settings.gemini_api_key.get_secret_value(),
                model_identifier=rag_settings.rag_model_identifier,
                timeout_seconds=rag_settings.rag_timeout_seconds,
            )
            prompt_loader = PromptLoader()
            rag_service = RagService(
                rerank_service, RagEvidenceStore(session_factory), prompt_loader,
                answer_provider, rag_settings,
            )
            if find_spec("langgraph") is not None:
                code_agent = CodeAnalysisAgent(
                    rerank_service, rag_service,
                    system_version=os.environ.get("CODE_ANALYSIS_SYSTEM_PROMPT_VERSION"),
                    user_version=os.environ.get("CODE_ANALYSIS_USER_PROMPT_VERSION"),
                )
                analyzer = PromptedAnalyzer(
                    prompt_loader, answer_provider,
                    versions={
                        name: (
                            os.environ.get(f"{name.upper()}_SYSTEM_PROMPT_VERSION"),
                            os.environ.get(f"{name.upper()}_USER_PROMPT_VERSION"),
                        )
                        for name in ("dependency_analysis", "architecture_analysis")
                    },
                )
                dependency_agent = DependencyAnalysisAgent(dependency_service, analyzer)
                agentic_service = AgenticWorkflow(
                    code_agent, dependency_agent,
                    ArchitectureAnalysisAgent(code_agent, dependency_agent, analyzer),
                    IntentClassifier(
                        prompt_loader, answer_provider,
                        system_version=os.environ.get("QUERY_CLASSIFIER_SYSTEM_PROMPT_VERSION"),
                        user_version=os.environ.get("QUERY_CLASSIFIER_USER_PROMPT_VERSION"),
                    ),
                    service,
                )
            evaluation_service = RetrievalEvaluationService(
                service, persistence, search_service, keyword_service,
                hybrid_service, rerank_service, DatasetLoader(),
                embedding_settings.embedding_model,
                MetadataHeuristicV1.version,
            )
    app.include_router(
        create_parse_router(
            CodeIntelligenceService(service, JavaParserAdapter(), persistence=persistence)
        )
    )
    app.include_router(create_embedding_router(embedding_service, index_status_service))
    app.include_router(create_search_router(search_service))
    app.include_router(create_keyword_router(keyword_service))
    app.include_router(create_hybrid_router(hybrid_service))
    app.include_router(create_rerank_router(rerank_service))
    app.include_router(create_rag_router(rag_service))
    app.include_router(create_agentic_router(agentic_service))
    app.include_router(create_chat_router(ChatService(agentic_service) if agentic_service else None))
    app.include_router(create_evaluation_router(evaluation_service))
    app.include_router(create_dependency_router(dependency_service))
    app.include_router(create_index_job_router(index_job_store, service))

    def openapi_schema():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version,
                                 description=app.description, routes=app.routes)
            for path in schema["paths"].values():
                for operation in path.values():
                    if "responses" in operation and "422" in operation["responses"]:
                        operation["responses"]["422"] = {
                            "description": "Request validation failed.",
                            "content": {"application/json": {"schema": {
                                "$ref": "#/components/schemas/ErrorResponse"
                            }}},
                        }
            def complete_error_examples(value):
                if isinstance(value, dict):
                    error = value.get("error")
                    if isinstance(error, dict) and "code" in error and "message" in error:
                        error.setdefault("request_id", "00000000-0000-4000-8000-000000000000")
                        error.setdefault("details", None)
                    for child in value.values():
                        complete_error_examples(child)
                elif isinstance(value, list):
                    for child in value:
                        complete_error_examples(child)
            complete_error_examples(schema["paths"])
            schema["components"]["schemas"]["StreamEvent"] = StreamEvent.model_json_schema()
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi_schema
    return app


app = create_app()
