# Retrieval evaluation

Phase 5.5 adds `POST /api/v1/evaluations/retrieval` to compare semantic, keyword, hybrid, and hybrid plus `metadata_heuristic_v1` reranking on the same versioned Java sample dataset. The module calls existing Python retrieval services; it does not call application HTTP endpoints internally or write to the repository or database. See the [dataset specification](../evaluation-dataset.md) for the pinned snapshot and labels.

## Prepare the sample repository

The tracked `backend/evaluation/sample_repository/` contains only non-sensitive demonstration Java code. Configure that directory as an approved alias, register it, and index its single file `src/main/java/example/CommerceService.java` using the existing chunk index endpoint. Apply cloud PostgreSQL migrations through `20261030_0004`, configure compatible Gemini embeddings, and generate embeddings for the repository. The benchmark requires `DATABASE_URL`, `GEMINI_API_KEY`, and the same model/dimension configuration used for stored document vectors. It performs no generative answering and does not use RAG prompt templates.

The dataset `java-code-search-v1` has version `1.0.0`, 13 manually labeled questions, and 12 expected method symbols. One question labels three relevant methods. Its `repository_snapshot_id` is a SHA-256 digest of the sorted persisted chunk manifest. The API recomputes this digest before and after the benchmark; a missing, extra, edited, or differently split chunk causes a controlled 409. This ensures the four strategies run against the same indexed source snapshot. A repository UUID is deliberately excluded from the digest so a fresh registration of the same sample source can match.

From `backend/`, set the approved sample alias before starting the API:

```sh
sample_root="$(pwd)/evaluation/sample_repository"
export ARCHMIND_APPROVED_REPOSITORY_ROOTS="[\"$sample_root\"]"
export ARCHMIND_REPOSITORY_ALIASES="{\"evaluation-sample\":\"$sample_root\"}"
```

Supply the managed database URL and Gemini key through your local secret environment, run `python -m alembic -c alembic.ini upgrade head` after review, and start Uvicorn. In `/docs`, register `{"name":"evaluation-sample","source_alias":"evaluation-sample"}`. Copy the returned repository UUID, index the sample Java file with `POST /api/v1/repositories/{repository_id}/chunks/index`, and call `POST /api/v1/repositories/{repository_id}/embeddings`. Confirm `GET /api/v1/repositories/{repository_id}/index/status` reports `READY` before evaluating.

## API

```http
POST /api/v1/evaluations/retrieval
Content-Type: application/json

{"repository_id":"<uuid>","dataset":"java-code-search-v1","top_k":5}
```

`top_k` must be 5–20. HTTP 200 returns dataset ID and version, repository snapshot ID, question count, a UTC evaluation timestamp, embedding model identifier, reranker version, and one metric record for each strategy in this stable order: `semantic`, `keyword`, `hybrid`, `hybrid_reranked`. Each record contains `recall_at_5`, `mrr`, and `average_latency_ms`. A valid but unknown dataset or repository returns 404; a snapshot mismatch or change returns 409; a repository over 1,000 indexed chunks returns 413; an invalid request or empty dataset returns 422. Provider and database failures propagate as controlled 429, 502, 503, or 504 responses. No partial scorecard is returned after a strategy failure.

## Metrics and reproducibility

Recall@5 is computed per question as the fraction of manually labeled relevant **symbols** appearing among the first five returned chunks, then averaged across questions. Multiple relevant symbols count separately. MRR is the average reciprocal rank of the first relevant symbol within the requested `top_k`; a missing relevant symbol contributes zero. Average latency is the arithmetic mean of measured end-to-end service-call durations in milliseconds for that strategy. Latency includes query embedding and database work where the strategy performs them. It is not a fixed benchmark number or an estimate.

The same questions, labels, repository ID, snapshot, and `top_k` are used for all four strategies. Hybrid and reranked hybrid invoke underlying retrieval services again, so the endpoint makes repeated provider calls and may incur cost and rate limits. The sample is intentionally small; run a wider benchmark before drawing production conclusions. Metrics depend on cloud network conditions, Gemini output, stored embeddings, and active retrieval settings. The response records the embedding model and reranker version, but it does not currently record every embedding input fingerprint or cloud provider revision. No improvement is claimed without live results.

In Swagger at `/docs`, execute the endpoint after indexing and embedding the pinned sample. Confirm HTTP 200, all four strategies, actual calculated metrics, timestamp, dataset version, model identifier, and reranker version. Unit tests exercise the arithmetic and API contract with controlled rankings; live metrics require the configured remote services. The endpoint never runs generative answer creation, so RAG system and user prompt versions are not part of this evaluation response. Future answer evaluations must record both versions.
