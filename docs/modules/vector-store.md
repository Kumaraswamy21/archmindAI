# Cloud PostgreSQL chunk and vector store

Phase 4.1 provides a relational store for AST-aware code chunks. Phase 4.2 generates Gemini embeddings and records their fingerprints. Phase 4.3 stores the validated vectors in the same managed PostgreSQL database with pgvector. There is still no semantic retrieval API or approximate similarity index. The FastAPI process runs locally; migrations remain explicit and provider-neutral.

## Configure and migrate

Install the backend dependencies from `backend/`:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Provide a managed cloud database URL through the environment or an ignored `.env` file. The backend does not load `.env` automatically; export its values in your shell or through a secret manager. Example shape:

```sh
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@MANAGED_HOST:5432/archmind'
export DATABASE_SSL_MODE=require
```

Replace every placeholder with your provider's values. The URL must identify a remote PostgreSQL host; local loopback hosts, Unix sockets, SQLite, and weaker SSL modes are rejected. `DATABASE_SSL_MODE` accepts `require`, `verify-ca`, or `verify-full`; prefer `verify-full` when the provider's certificate chain and hostname can be verified. Credentials and full connection strings are never included in application error responses or SQL logging.

Run migrations manually after reviewing the revision in `backend/alembic/versions/`:

```sh
cd backend
python -m alembic -c alembic.ini upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API does not create tables or run migrations on startup. Missing `DATABASE_URL` leaves Phase 1–3 endpoints available; the persistence endpoints return HTTP 503 with `DATABASE_NOT_CONFIGURED`. A configured but unreachable or unmigrated database returns HTTP 503 with `DATABASE_UNAVAILABLE`.

## Schema and transactions

`repositories` stores UUID, name, approved source alias, status, and registration time. It deliberately omits the absolute source root. `code_chunks` stores the Phase 3 source text and structural metadata: repository and file identity, language, chunk kind, symbol and qualified names, parent symbol, package, imports, annotations, one-based lines, UTF-8 byte offsets, parent chunk ID, sequence, split strategy, contextual header, SHA-256 content hash, and creation time. A foreign key links each chunk to its repository. Indexes support repository/file reads, source ordering, and content-hash lookup. Embeddings live in the separate `chunk_embeddings` table.

Each index request opens a SQLAlchemy session and one transaction. It inserts the repository metadata if absent, removes stale chunks for that file, and inserts current chunks with PostgreSQL `ON CONFLICT DO NOTHING`. Repeating an index of unchanged content preserves the same rows and IDs. Indexing an edited file replaces the old file's chunks atomically. The session closes after each operation. The engine uses SSL, a five-connection pool with at most two overflow connections per process, a five-second pool wait and connection timeout, and pre-ping for stale connections.

Since Phase 6.6.1, registration writes a repository row immediately when managed PostgreSQL is configured. The same UUID can be listed and used for source operations after restart as long as its alias remains approved and available. No absolute source root is stored. Without database configuration, the earlier process-local registry remains available.

## API

First register an approved local source alias using `POST /api/v1/repositories`. Then:

```http
POST /api/v1/repositories/{repository_id}/chunks/index
Content-Type: application/json

{"file_path":"src/main/java/com/example/OrderService.java","max_chunk_chars":1500}
```

HTTP 200 returns `repository_id`, `file_path`, and `total_chunks`. The request reuses the secure file reader, Java parser, symbol extractor, and AST chunker. It does not execute or modify repository code. A missing/invalid file uses the existing preview endpoint's 400, 403, 404, 413, or 422 errors; database or source unavailability returns 503.

```http
GET /api/v1/repositories/{repository_id}/chunks?limit=50&offset=0
```

HTTP 200 returns `repository_id`, `total_chunks`, `limit`, `offset`, and `chunks`. Chunks use the Phase 3 `JavaChunk` schema and are ordered by relative file path, start byte, and chunk ID. `total_chunks` counts all persisted chunks before pagination. The default page size is 50; the allowed range is 1–100. Offset must be nonnegative. Unknown repository IDs return 404, invalid pagination returns 422, and database failures return 503. Neither endpoint exposes connection details or absolute source paths.

## Verification and limits

Unit tests use mocks and SQL compilation without cloud access. The opt-in remote integration test runs only when `ARCHMIND_RUN_REMOTE_DB_TESTS=1` and `ARCHMIND_TEST_DATABASE_URL` names a separate remote database beginning with `test_` or ending with `_test`. Apply the migration to that isolated database explicitly before running the test. Never point it at production. The test verifies committed reads through a new engine, idempotence, and foreign-key enforcement, then removes only its own rows.

Phase 4.1 stores chunk text and metadata only. It does not track source changes automatically. Reindex a file after it changes; stale chunks for that file are replaced then. Phase 4.2 adds embedding generation and metadata as described below.

## Gemini embedding generation

Set `GEMINI_API_KEY` only when the administrator has approved sending persisted source chunks to Google's Gemini API. The API key stays in the environment or secret manager and is never logged or returned. Apply Alembic revision `20261030_0002` explicitly with `python -m alembic -c alembic.ini upgrade head`; API startup never migrates.

The default model is `gemini-embedding-2`, with a requested output dimension of 768. Configure `EMBEDDING_MODEL` and `EMBEDDING_DIMENSION` to change them. The adapter checks the number of returned vectors, their actual dimensions, and finite numeric values; it does not assume the request was honored. The default batch size is 16 (`EMBEDDING_BATCH_SIZE`, range 1–100). Each batch uses one SDK call with a separate `Content` object per chunk, preventing Gemini Embedding 2 from aggregating the whole batch into one vector. `EMBEDDING_REQUEST_TIMEOUT_SECONDS` defaults to 30; `EMBEDDING_MAX_RETRIES` defaults to 2 additional attempts with exponential backoff starting at `EMBEDDING_RETRY_BACKOFF_SECONDS=0.25`. Rate limits, timeouts, and transient transport/server failures are retried within those bounds. Authentication, input, and dimension errors are not retried. These defaults are configurable and bounded in Pydantic Settings.

The input builder uses the Phase 3 `contextual_header`, qualified name, relative file path, chunk type, and exact chunk content. It omits broad file-level imports and other unrelated source. Its format version is `1.0.0`; SHA-256 of the version and exact input text is stored as `input_hash`. The `chunk_embeddings` table records `chunk_id`, model identifier, output dimension, input format version, input hash, `COMPLETED` / `FAILED` / `SKIPPED_EMPTY` / `PENDING` status, a sanitized error code, vector, and timestamps. A foreign key cascades vector cleanup when a stale chunk is replaced. A completed row with the same model, dimension, input version, hash, and a stored vector skips a repeat Gemini call. Changing model, dimension, chunk content, or input format triggers new generation.

The provider interface has `embed_documents(texts)` and `embed_query(text)`. The document input follows Gemini Embedding 2's text retrieval structure; a query uses a code-retrieval task prefix. Query embedding is exposed to internal callers only in this phase—there is no search API. Phase 4.3 persists validated document vectors. Migration `20261030_0003` marks Phase 4.2 `COMPLETED` metadata as `PENDING` because those rows had no vectors; the next embedding run regenerates them.

```http
POST /api/v1/repositories/{repository_id}/embeddings
```

The synchronous response is `{"repository_id":"uuid","total_chunks":20,"generated":18,"skipped":2,"failed":0,"model":"gemini-embedding-2"}`. `total_chunks` counts persisted chunks; `skipped` includes unchanged and empty-content chunks; `failed` counts chunks in failed batches. Partial failures return HTTP 200 with counts so successful batches are visible. Unknown repositories return 404. Missing database or API configuration returns 503. If every chunk fails, exhausted rate limits return 429, provider/dimension errors return 502, and exhausted timeouts return 504. Internal error messages do not expose provider responses, embedding inputs, vectors, API keys, or database credentials.

The service reads chunks in pages of 100, consults existing fingerprints, and submits only changed nonempty inputs in bounded batches. Google currently documents `gemini-embedding-2` as a stable model and recommends separate `Content` objects for independent embeddings; see the [official embedding guide](https://ai.google.dev/gemini-api/docs/embeddings). A stable model alias can change behind the provider's API without changing its identifier; operators should set a new model identifier or intentionally bump the input format when re-embedding is required. Character-based chunk limits do not guarantee that every input fits the model's token limit, so an oversized provider request can fail as a batch without truncation.

Unit tests mock the Gemini SDK and database interfaces and require no cloud access. A harmless live query smoke test runs only with `ARCHMIND_RUN_GEMINI_SMOKE_TEST=1` and a valid `GEMINI_API_KEY`. Cloud persistence and restart checks require an explicitly configured isolated remote test database as described above.

## pgvector storage and index status

Review and explicitly apply revision `20261030_0003` before starting a vector-enabled API process. It runs `CREATE EXTENSION IF NOT EXISTS vector`, adds a nullable `VECTOR` column and `generated_at`, expands the primary key to `(chunk_id, model_identifier, output_dimension, input_hash)`, and adds dimension and completed-vector checks. The application never creates the extension in a request. Before migration, an administrator can inspect availability with:

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'vector';
```

If `vector` is absent, enable pgvector through the managed provider's extension controls or support process. If it is available but the migration role lacks `CREATE EXTENSION` privilege, have a provider administrator enable it in the target database, then rerun the reviewed migration. This workspace cannot verify extension availability without a cloud connection. The downgrade intentionally refuses to discard historical vectors; a rollback needs a reviewed data migration.

The column uses pgvector's SQLAlchemy `VECTOR()` type with row-level checks: the actual vector dimension must equal `output_dimension`, and the configured dimension must be 128–3072. A `COMPLETED` row must have a vector. The default model/dimension is `gemini-embedding-2` / 768; the provider response is checked for dimension, finite values, and a nonzero vector before insertion, and PostgreSQL checks dimension again. Different model identifiers, dimensions, and input hashes occupy distinct rows. The active index status counts only rows for the currently configured model, dimension, and exact current input hash, so incompatible embedding spaces are never combined. A failed attempt for changed input creates a separate row and cannot overwrite an older completed vector. Phase 4.2 metadata is marked `PENDING` by the migration and regenerated on the next call to `POST /api/v1/repositories/{repository_id}/embeddings`.

```http
GET /api/v1/repositories/{repository_id}/index/status
```

HTTP 200 returns `repository_id`, `total_chunks`, `embedded_chunks`, `pending_chunks`, `failed_chunks`, and `index_status`. `READY` means every current chunk has a stored vector for the configured model/dimension/input; `EMPTY` means no persisted chunks. `PENDING`, `PARTIAL`, and `FAILED` describe incomplete coverage. The counts are computed from persisted chunks, current input fingerprints, metadata status, and stored vectors—not from a cached API counter. Unknown repositories return 404, unavailable databases return 503, and the response never includes vectors.

The initial sample repository uses PostgreSQL's exact-search baseline. No HNSW or IVFFlat index is created and no retrieval endpoint exists yet. A future search should filter by model identifier and dimension and use cosine distance (`<=>`) only among compatible vectors. Exact search preserves full recall for small datasets. HNSW would trade recall for speed, consume more memory, and cost more to build; `vector_cosine_ops` would be the relevant cosine operator class. pgvector currently limits a standard HNSW `vector` index to 2,000 dimensions, while the plain vector type stores more, so a 3,072-dimensional configuration would need an explicit future index strategy such as half precision or a smaller output dimension. See the [pgvector project documentation](https://github.com/pgvector/pgvector) and [Python SQLAlchemy adapter](https://github.com/pgvector/pgvector-python).

The opt-in remote test checks extension availability, migration revision, vector storage, model-scoped reads, and visibility through a new connection pool. It runs only against an explicitly configured isolated managed test database and removes only its own rows. No live extension or API restart check can be claimed until that test database is supplied.
