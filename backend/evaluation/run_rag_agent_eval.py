"""Opt-in live evaluation in a disposable schema on the configured managed PostgreSQL."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.modules.project_indexing.persistence import IndexJobStore
from app.modules.project_indexing.worker import IndexWorker
from app.modules.vector_store.config import EmbeddingSettings
from app.modules.vector_store.embeddings.gemini import GeminiEmbeddingProvider


def _response(response, stage: str) -> dict:
    if response.status_code not in {200, 201, 202}:
        try:
            code = response.json().get("error", {}).get("code", "HTTP_ERROR")
        except ValueError:
            code = "HTTP_ERROR"
        raise RuntimeError(f"{stage} failed ({code}, HTTP {response.status_code}).")
    return response.json()


def run() -> dict:
    if os.environ.get("ARCHMIND_RUN_EVALUATION_REMOTE") != "1":
        raise RuntimeError(
            "Set ARCHMIND_RUN_EVALUATION_REMOTE=1 for the isolated remote evaluation."
        )
    if not os.environ.get("DATABASE_URL") or not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("Managed DATABASE_URL and GEMINI_API_KEY are required.")
    url = make_url(os.environ["DATABASE_URL"])
    if url.host and "-pooler" in url.host:
        os.environ["DATABASE_URL"] = url.set(
            host=url.host.replace("-pooler", "")
        ).render_as_string(
            hide_password=False,
        )
    schema = "archmind_evaluation_" + uuid4().hex[:12]
    admin = create_database_engine(DatabaseSettings())
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(
                    f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'
                )
            )
        created = True
        os.environ["PGOPTIONS"] = f"-csearch_path={schema},public"
        command.upgrade(Config("alembic.ini"), "head")
        sample = Path("evaluation/spring_sample_repository").resolve()
        alias = "evaluation-spring-" + uuid4().hex[:8]
        os.environ["ARCHMIND_APPROVED_REPOSITORY_ROOTS"] = json.dumps([str(sample)])
        os.environ["ARCHMIND_REPOSITORY_ALIASES"] = json.dumps({alias: str(sample)})
        os.environ["ARCHMIND_ENV"] = "test"
        app = create_app()
        try:
            with app.state.db_engine.connect() as connection:
                if connection.scalar(text("SELECT current_schema()")) != schema:
                    raise RuntimeError(
                        "Evaluation database connection did not use its isolated schema."
                    )
            settings = EmbeddingSettings()
            provider = GeminiEmbeddingProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                model_identifier=settings.embedding_model,
                output_dimension=settings.embedding_dimension,
                timeout_seconds=settings.embedding_request_timeout_seconds,
            )
            sessions = create_session_factory(app.state.db_engine)
            worker = IndexWorker(
                IndexJobStore(sessions),
                app.state.repository_service,
                provider,
                settings,
            )
            with TestClient(app) as client:
                repository = _response(
                    client.post(
                        "/api/v1/repositories",
                        json={
                            "name": "Spring evaluation sample",
                            "source_alias": alias,
                        },
                    ),
                    "repository registration",
                )
                repository_id = repository["repository_id"]
                job = _response(
                    client.post(f"/api/v1/repositories/{repository_id}/index"),
                    "index queue",
                )
                if not worker.process_one():
                    raise RuntimeError("Index worker did not claim the evaluation job.")
                completed = _response(
                    client.get(
                        f"/api/v1/repositories/{repository_id}/index/jobs/{job['job_id']}"
                    ),
                    "index status",
                )
                if completed["status"] != "COMPLETED":
                    raise RuntimeError(
                        f"Indexing failed ({completed.get('error_code')})."
                    )
                retrieval = _response(
                    client.post(
                        "/api/v1/evaluations/retrieval",
                        json={
                            "repository_id": repository_id,
                            "dataset": "java-spring-search-v1",
                            "top_k": 5,
                        },
                    ),
                    "retrieval evaluation",
                )
                agents = _response(
                    client.post(
                        "/api/v1/evaluations/rag-agents",
                        json={
                            "repository_id": repository_id,
                            "dataset": "java-spring-agent-v1",
                            "top_k": 5,
                        },
                    ),
                    "agent evaluation",
                )
                return {
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                    "repository": "tracked synthetic Java/Spring sample",
                    "execution": "live managed PostgreSQL and configured Gemini",
                    "retrieval": retrieval,
                    "agents": agents,
                }
        finally:
            app.state.db_engine.dispose()
    finally:
        if created:
            try:
                with admin.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                admin.dispose()
        else:
            admin.dispose()


def main() -> None:
    try:
        report = run()
    except Exception as exc:
        # Never print SQLAlchemy connection details or provider error payloads.
        raise SystemExit(
            f"Evaluation did not complete: {type(exc).__name__}."
        ) from None
    destination = Path("../.local-docs/phase-07/evaluation-report.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Measured evaluation report written to {destination}.")


if __name__ == "__main__":
    main()
