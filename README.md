# ArchMind AI

ArchMind AI is a read-only repository intelligence and architecture analysis platform.

The Phase 6.6.1 backend exposes [frontend project contracts](docs/api/frontend-contract.md):
paginated repository listing, persisted project metadata, approved alias discovery,
registration with independent display names, a shared error envelope, and explicit CORS origins.
With `DATABASE_URL` configured and migrations applied, project IDs survive backend restarts.
Phase 6.6.2 adds [durable project indexing jobs](docs/modules/project-onboarding.md). Start the separate backend worker to process queued jobs; the API returns immediately and exposes job progress.

- [Backend setup and API usage](backend/README.md)
- [Runtime LLM prompt versioning standard](docs/prompt-management.md)
- [Prompt changelog](docs/prompt-changelog.md)
- [Java code intelligence API](docs/modules/code-intelligence.md)
- [Semantic code search API](docs/modules/semantic-search.md)
- [Keyword and symbol search API](docs/modules/retrieval.md)
- [Grounded RAG API](docs/modules/rag.md)
- [Retrieval evaluation](docs/modules/evaluation.md)
- [Langfuse observability](docs/observability/langfuse.md)

Phase 5.4 adds Gemini-powered grounded answers with released runtime prompt templates, a central registry, and a shared loader. See the [prompt versioning standard](docs/prompt-management.md).

## Approved local repository configuration

An administrator configures repository aliases before starting the backend. Set `ARCHMIND_APPROVED_REPOSITORY_ROOTS` to a JSON array of absolute directory paths and `ARCHMIND_REPOSITORY_ALIASES` to a JSON object mapping public aliases to absolute repository directory paths. For example:

```sh
export ARCHMIND_APPROVED_REPOSITORY_ROOTS='["/srv/archmind/approved"]'
export ARCHMIND_REPOSITORY_ALIASES='{"sample-ecommerce":"/srv/archmind/approved/ecommerce-service"}'
```

Replace the example paths with directories that already exist. With no aliases configured, registration is unavailable. API clients submit only a repository name and an alias; absolute paths stay inside the backend. See the [repository ingestion contract](docs/modules/repository-ingestion.md) and [backend setup](backend/README.md).

After registration, `GET /api/v1/repositories/{repository_id}/files` lists `.java`, `.py`, and `.ts` files as relative paths. Optional `ARCHMIND_DISCOVERY_MAX_FILES` and `ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES` settings default to 10,000 files and 10 MiB per file. Exceeding either limit fails the discovery request with HTTP 413.

`GET /api/v1/repositories/{repository_id}/files/content?path=relative/source.py` reads a supported UTF-8 source file through a repository-relative path. See the [source content security contract](docs/modules/repository-ingestion.md#source-content-api).

## Ingestion workflow

1. Configure approved roots and aliases as above, then start the backend using the [backend instructions](backend/README.md).
2. Register an alias with `POST /api/v1/repositories` and keep the returned `repository_id`.
3. Discover source metadata with `GET /api/v1/repositories/{repository_id}/files`.
4. Read an individual supported file with `GET /api/v1/repositories/{repository_id}/files/content?path=relative/source.py`.
5. Summarize the current source files with `POST /api/v1/repositories/{repository_id}/scan`.

The scan returns `COMPLETED`, file and byte totals, and counts for `java`, `python`, and `typescript`. It is synchronous, read-only, and recomputed on every request. The full [Phase 2 API contract](docs/modules/repository-ingestion.md) documents errors and limits.

## Java code intelligence

After registering a repository, use `POST /api/v1/repositories/{repository_id}/parse` with a relative `.java` `file_path` to extract top-level classes, direct methods, one-based line numbers, and syntax-error presence. The endpoint reuses the secure source-file reader. See the [code intelligence contract](docs/modules/code-intelligence.md).

Use `GET /api/v1/repositories/{repository_id}/symbols?file_path=relative/Source.java` for package and import names, class and interface symbols, methods, constructors, signatures, parameters, annotations, and enclosing types. These are source-code facts, not resolved dependencies or runtime behavior.

Use `POST /api/v1/repositories/{repository_id}/chunks/preview` with the same relative Java `file_path` to preview method, constructor, and leaf-class chunks. Optional `max_chunk_chars` overrides the default 4,000-character limit (`ARCHMIND_MAX_CHUNK_CHARS`). Oversized declarations split into ordered parts with parent IDs and exact source offsets; source outside chunks is reported in `excluded_spans`. Chunks are not stored. See the [chunk preview contract](docs/modules/code-intelligence.md#java-chunk-preview-api).

## Cloud chunk persistence

Phase 4.1 stores generated chunks in an administrator-provided managed PostgreSQL database. Configure `DATABASE_URL` and `DATABASE_SSL_MODE=require` (or a stronger certificate-verifying mode), run the explicit Alembic migration, then register a repository and call `POST /api/v1/repositories/{repository_id}/chunks/index` with `{"file_path":"relative/Source.java"}`. Retrieve persisted results with `GET /api/v1/repositories/{repository_id}/chunks?limit=50&offset=0`. The index operation reuses the AST preview service and replaces stale chunks for that file atomically. See the [cloud chunk store guide](docs/modules/vector-store.md) for setup, errors, and restart behavior.

## Gemini embedding generation

Phase 4.2 adds `POST /api/v1/repositories/{repository_id}/embeddings` for persisted chunks. Set `GEMINI_API_KEY`, `DATABASE_URL`, and the optional `EMBEDDING_*` settings. Phase 4.3 adds cloud pgvector storage and `GET /api/v1/repositories/{repository_id}/index/status`; explicitly review and apply Alembic revision `20261030_0003` before use. The APIs return counts and status, never vectors. See the [embedding architecture, extension setup, and index policy](docs/modules/vector-store.md#pgvector-storage-and-index-status).

## Semantic code search

After indexing Java chunks and generating compatible embeddings, call `POST /api/v1/search/semantic` with `{"repository_id":"<uuid>","query":"Where is payment processing implemented?","top_k":5}`. Results include actual cosine similarity scores and repository-relative source references. See the [semantic search contract and evaluation instructions](docs/modules/semantic-search.md).

## Keyword and symbol search

Phase 5.1 adds `POST /api/v1/search/keyword` for exact Java symbol names and PostgreSQL full-text matching of indexed chunks. Review and apply migration `20261030_0004` explicitly. Search is scoped to one repository and works without embeddings or Gemini credentials. See the [retrieval contract](docs/modules/retrieval.md).

Phase 5.2 adds `POST /api/v1/search/hybrid`, which fuses semantic and keyword candidate ranks without mixing their original score scales. Both strategies must succeed. See the [hybrid retrieval contract](docs/modules/retrieval.md#hybrid-retrieval-phase-52).

Phase 5.3 adds `POST /api/v1/search/rerank`, a deterministic metadata heuristic over the bounded hybrid results. It reports each candidate's original and new ranks and the score used for sorting. See the [reranking contract](docs/modules/retrieval.md#metadata-heuristic-reranking-phase-53).

Phase 5.4 adds `POST /api/v1/ask`, which answers from bounded reranked source evidence and returns checked file-and-line citations, limitations, prompt versions, and model identifier. See the [RAG API and setup](docs/modules/rag.md).

Phase 5.5 adds `POST /api/v1/evaluations/retrieval` for a versioned, snapshot-pinned comparison of semantic, keyword, hybrid, and reranked hybrid search. Prepare the non-sensitive [sample dataset](docs/evaluation-dataset.md) and use the [evaluation guide](docs/modules/evaluation.md). No generative answer is created by this benchmark.

## Repository analysis workflow

Phase 6.1 adds `POST /api/v1/agents/analyze`. Phase 6.2 adds intent-aware routing. Phase 6.4 routes code questions to grounded RAG, dependency questions to persisted static edges, and architecture questions to bounded code-and-dependency synthesis; unclear questions request clarification. Responses include executed agents, evidence count, duration, and selected prompt versions. Configure the managed database and Gemini as for `/api/v1/ask`, index and embed code, and run the dependency scan before live analysis. See the [agentic workflow contract](docs/modules/agentic.md).

Phase 6.3 adds read-only static Java dependency scanning with `POST /api/v1/repositories/{repository_id}/dependencies/scan` and repository-scoped listing with `GET /api/v1/repositories/{repository_id}/dependencies`. Apply the explicit cloud PostgreSQL migration before scanning. The [dependency intelligence guide](docs/modules/dependency-intelligence.md) defines supported edges and resolution limits.

Phase 6.5 adds a deterministic evidence verification stage to `POST /api/v1/agents/analyze`. Responses now report claim-level citation checks, unsupported claims, and at most one correction pass. Specialist prompt versions 1.1.0 require inline evidence IDs; see the [verification contract](docs/modules/evidence-verification.md).
