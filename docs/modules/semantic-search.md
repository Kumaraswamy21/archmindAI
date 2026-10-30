# Semantic code search

Phase 4.4 adds exact cosine similarity retrieval over Java AST chunks stored in the managed PostgreSQL database. It reuses the Gemini query embedding provider, Phase 4 chunk and embedding tables, and the existing SSL database engine. It does not call a generative LLM, add prompts, or execute repository code.

## Setup and workflow

Apply the reviewed pgvector migration through revision `20261030_0003` to the managed database. Configure `DATABASE_URL`, `DATABASE_SSL_MODE`, `GEMINI_API_KEY`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSION` as described in the [vector store guide](vector-store.md). The document and query model and dimension must match. Register an approved repository alias, index its Java files with `POST /api/v1/repositories/{repository_id}/chunks/index`, then generate stored embeddings with `POST /api/v1/repositories/{repository_id}/embeddings`. Check coverage with `GET /api/v1/repositories/{repository_id}/index/status` before searching. No local database is used.

```http
POST /api/v1/search/semantic
Content-Type: application/json

{"repository_id":"<uuid>","query":"Where is payment processing implemented?","top_k":5}
```

HTTP 200 returns the normalized query, repository UUID, and ranked results containing chunk ID, repository-relative file path, symbol name, one-based start and end lines, exact cosine similarity (`1 - cosine_distance`), and chunk content. `top_k` defaults to 5 and must be 1–20. Queries are trimmed, must contain text, and cannot exceed 2,000 characters. Invalid bodies receive FastAPI's standard 422 response. Unknown repositories return 404. A repository with no compatible current embeddings returns 409. A configured query provider whose model or dimension differs from document settings returns 409. Provider rate limits, failures, and timeouts return controlled 429, 502, and 504 responses; missing configuration or database failures return 503. Responses never expose vectors, credentials, database URLs, or absolute source paths.

## Retrieval rules

The query is embedded once with the same configured model and output dimension used for documents. The service verifies vector length, finite values, and nonzero content. Current chunk input hashes are rebuilt with the existing deterministic embedding input builder. A parameterized SQLAlchemy query joins chunks to embeddings and filters by repository ID, exact chunk/input-hash pair, model, dimension, input format version, completed status, and non-null vector. PostgreSQL's pgvector cosine distance performs the ranking. Equal distances are ordered by relative file path, start byte, then chunk ID. The query applies a five-second PostgreSQL statement timeout. Chunks are processed in pages of 100; each page returns at most `top_k`, and the service merges those page leaders into the global top-k. This is an exact-search baseline without HNSW, keyword search, or reranking.

Search is limited to current persisted chunks. Reindex changed files and regenerate embeddings before expecting them in results. Some chunks can be searchable while others remain pending; the index status endpoint shows coverage. Semantic similarity suggests relevant source locations, but it does not prove call relationships, runtime behavior, or architectural intent. Inspect the referenced code before drawing conclusions.

## Lightweight evaluation

`backend/evaluation/sample_repository/` contains non-sensitive Java methods, and `backend/evaluation/semantic_search_cases.json` retains three earlier questions with one expected symbol each. Phase 5.5 adds a broader, snapshot-pinned dataset. Configure the sample directory as an approved repository alias, register it, index `src/main/java/example/CommerceService.java`, generate embeddings, and run:

```sh
cd backend
python evaluation/evaluate_semantic_search.py <repository-uuid>
```

The runner calls the real local API for all three questions and reports Recall@5, mean reciprocal rank (MRR), and average end-to-end query latency in milliseconds. These figures depend on the configured model, remote database, network, and provider. Unit tests use controlled scores to check sorting and isolation; they do not substitute for this live evaluation. A separate opt-in test uses an isolated remote test database to check actual pgvector cosine scores and repository filtering. Set `ARCHMIND_RUN_REMOTE_DB_TESTS=1` and `ARCHMIND_TEST_DATABASE_URL` only for a dedicated migrated test database whose name starts with `test_` or ends with `_test`.

## Limits

Search currently reads current chunks to recompute input hashes, then runs exact similarity queries per 100-chunk page. This keeps the result correct for the current content but adds work as repositories grow. Registration remains in memory, while indexed chunks and embeddings persist in PostgreSQL. There is no approximate index, semantic answer generation, keyword search, hybrid search, or relevance threshold. The current ranking may return weak matches when only weak matches exist; inspect actual scores and source references.
