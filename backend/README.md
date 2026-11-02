# ArchMind AI backend

Minimal FastAPI service for the ArchMind AI API.

Grounded RAG uses released system and user templates, a central registry, and a shared loader under the [project prompt management standard](../docs/prompt-management.md).

## Set up the environment

Run these commands from the `backend` directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

No environment variables are required. See `.env.example`.
To register local repositories, configure approved roots and aliases before startup as described in the [repository ingestion guide](../docs/modules/repository-ingestion.md). No aliases are enabled by default.
For a browser frontend, configure `ARCHMIND_FRONTEND_ORIGINS` as a JSON list of exact origins. Project registration and metadata persist in managed PostgreSQL when `DATABASE_URL` is configured. See the [frontend API contract](../docs/api/frontend-contract.md).
For managed full-repository Java indexing, apply Alembic revision `20261031_0006`, start `.venv/bin/python -m app.modules.project_indexing.worker` in a separate process with the same environment, and call `POST /api/v1/repositories/{repository_id}/index`. Poll its job endpoint. See [project onboarding](../docs/modules/project-onboarding.md).

## Start the server

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The OpenAPI schema is available at <http://127.0.0.1:8000/openapi.json> and Swagger UI at <http://127.0.0.1:8000/docs>.
The health check is available at `GET /api/v1/health` and returns `{"status":"healthy","service":"archmind"}`.
`POST /api/v1/questions/validate` accepts a JSON object with a `question` string. It trims surrounding whitespace and returns `{"valid":true,"normalized_question":"..."}`. Empty, missing, or overlong questions receive HTTP 422.
`GET /api/v1/questions/examples/order-service` returns an example question. `service-dependencies` is another available ID. An unknown ID returns HTTP 404 with an `error` object containing a `code` and `message`.
`POST /api/v1/repositories` registers a configured alias and returns HTTP 201. With a configured managed database, project metadata and UUIDs persist across restarts. Repository paths are never returned.
`GET /api/v1/repositories/{repository_id}/files` lists supported source files in that registered repository. See the [repository ingestion guide](../docs/modules/repository-ingestion.md) for filtering, limits, and errors.
`GET /api/v1/repositories/{repository_id}/files/content?path=relative/source.py` reads one supported UTF-8 source file without modifying it. The same per-file size limit applies; see the [security and error contract](../docs/modules/repository-ingestion.md#source-content-api).
`POST /api/v1/repositories/{repository_id}/scan` synchronously summarizes the currently discoverable source files by count, bytes, and language. See the [complete ingestion workflow](../README.md#ingestion-workflow).
`POST /api/v1/repositories/{repository_id}/parse` accepts `{"file_path":"relative/Source.java"}` and returns top-level Java classes, direct methods, line numbers, and syntax-error presence. See the [code intelligence guide](../docs/modules/code-intelligence.md).
`GET /api/v1/repositories/{repository_id}/symbols?file_path=relative/Source.java` extracts Java package, imports, classes, interfaces, methods, constructors, parameters, annotations, and enclosing type context. See the same [code intelligence guide](../docs/modules/code-intelligence.md).
`POST /api/v1/repositories/{repository_id}/chunks/preview` accepts `{"file_path":"relative/Source.java","max_chunk_chars":1500}` and returns AST-aware Java method, constructor, and leaf-class chunks with source text and structural metadata. The character limit is optional; `ARCHMIND_MAX_CHUNK_CHARS` sets its default to 4,000. Oversized declarations return ordered parts and exact byte offsets, while `excluded_spans` reports source outside emitted chunks. See the [chunk preview contract](../docs/modules/code-intelligence.md#java-chunk-preview-api).
`POST /api/v1/repositories/{repository_id}/chunks/index` persists the current Java file's previewed chunks to managed PostgreSQL. `GET /api/v1/repositories/{repository_id}/chunks?limit=50&offset=0` returns persisted chunks. Set `DATABASE_URL`, then run `python -m alembic -c alembic.ini upgrade head` explicitly before indexing. See the [cloud chunk store guide](../docs/modules/vector-store.md). Without database configuration, persistence endpoints return HTTP 503 while earlier endpoints remain available.
`POST /api/v1/repositories/{repository_id}/embeddings` generates and stores Gemini embeddings for persisted chunks when `GEMINI_API_KEY` is set. Configure model, dimension, batching, timeout, and retries with `EMBEDDING_*` settings in `.env.example`; run Alembic revision `20261030_0003` explicitly after enabling pgvector on the managed database. `GET /api/v1/repositories/{repository_id}/index/status` reports actual vector coverage. Neither API returns vectors. See the [pgvector guide](../docs/modules/vector-store.md#pgvector-storage-and-index-status).
`POST /api/v1/search/semantic` accepts a repository UUID, developer question, and optional `top_k` (1–20), then returns current repository chunks ranked by exact pgvector cosine similarity. It requires compatible persisted embeddings and the configured query provider. See the [semantic search guide](../docs/modules/semantic-search.md) for the API, errors, and lightweight evaluation.
`POST /api/v1/search/keyword` searches indexed Java chunks by exact symbol, enclosing symbol, and PostgreSQL full-text terms. Apply Alembic revision `20261030_0004` explicitly, then use `{"repository_id":"<uuid>","query":"OrderService","top_k":5}`. It needs the cloud database but no Gemini key. See the [keyword retrieval guide](../docs/modules/retrieval.md).
`POST /api/v1/search/hybrid` combines bounded semantic and keyword candidates using reciprocal rank fusion. Set `HYBRID_RRF_CONSTANT` and `HYBRID_CANDIDATE_POOL` only if the defaults of 60 and 20 need changing. Both underlying searches must succeed. See the [hybrid retrieval contract](../docs/modules/retrieval.md#hybrid-retrieval-phase-52).
`POST /api/v1/search/rerank` applies the deterministic `metadata_heuristic_v1` to a bounded hybrid candidate list and returns original and new ranks with calculated scores. It requires the same cloud database and query-embedding configuration as hybrid search. See the [reranking contract](../docs/modules/retrieval.md#metadata-heuristic-reranking-phase-53).
`POST /api/v1/ask` answers a repository question from reranked source evidence with verified file-and-line citations. Configure `RAG_*` settings and released prompt versions as needed; the question and private source content are runtime inputs. See the [grounded RAG contract](../docs/modules/rag.md) and [prompt versioning policy](../docs/prompt-management.md).
`POST /api/v1/evaluations/retrieval` measures Recall@5, MRR, and end-to-end latency for semantic, keyword, hybrid, and reranked hybrid search on the pinned `java-code-search-v1` sample. Register and index `evaluation/sample_repository/`, then generate embeddings before running it. See the [evaluation guide](../docs/modules/evaluation.md) and [dataset specification](../docs/evaluation-dataset.md).
`POST /api/v1/agents/analyze` classifies intent, then routes to grounded code, persisted dependency, or architecture synthesis specialists. Architecture synthesis gathers code and dependency evidence with at most two concurrent workers. Configure managed PostgreSQL, indexed embeddings, Gemini, and explicit migrations; run the dependency scan first. Optional `QUERY_CLASSIFIER_*`, `CODE_ANALYSIS_*`, `DEPENDENCY_ANALYSIS_*`, and `ARCHITECTURE_ANALYSIS_*` prompt version settings select released versions. Without configuration the endpoint returns HTTP 503. See the [agentic workflow guide](../docs/modules/agentic.md).
`POST /api/v1/repositories/{repository_id}/dependencies/scan` extracts static Java import, inheritance, injection, and direct-call edges and persists them in managed PostgreSQL. `GET /api/v1/repositories/{repository_id}/dependencies` lists repository-scoped edges with optional symbol, type, direction, and limit filters. Apply Alembic revision `20261031_0005` explicitly before use. See the [dependency intelligence guide](../docs/modules/dependency-intelligence.md).
`POST /api/v1/agents/analyze` now verifies every returned claim's inline evidence IDs against included repository evidence and Phase 5 citation rules. Unsupported claims are removed by one bounded correction pass or the answer becomes insufficient; `verification_status`, `unsupported_claims`, and `claim_verifications` describe the result. Active specialist prompts are 1.1.0; prior releases remain available. See the [evidence verification guide](../docs/modules/evidence-verification.md).

## Run tests

```sh
python -m pytest
```

FastAPI generates the OpenAPI schema from the application metadata and registered routes. Swagger UI reads that schema to display the interactive API documentation.
