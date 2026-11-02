# Keyword and symbol retrieval

Phase 5.1 adds repository-scoped keyword search over indexed Java chunks in managed PostgreSQL. It complements [semantic search](semantic-search.md): an exact Java symbol can be found without an embedding API call, while PostgreSQL full-text search can find words in chunk content and structural context. Phase 5.2 combines their ranked candidates with reciprocal rank fusion (RRF).

## Setup

Review and explicitly apply Alembic revision `20261030_0004` to the managed PostgreSQL database:

```sh
cd backend
python -m alembic -c alembic.ini upgrade head
```

The migration adds a stored generated `tsvector` column to `code_chunks`. PostgreSQL computes it from `symbol_name`, `qualified_name`, `contextual_header`, and `content` for existing and future rows. A GIN index supports full-text matching. Repository-scoped B-tree indexes support exact symbol, qualified-name, and enclosing-symbol lookups. Migrations do not run during API startup. Index Java files with `POST /api/v1/repositories/{repository_id}/chunks/index` before searching. Keyword search requires `DATABASE_URL` but does not require `GEMINI_API_KEY` or embeddings.

## API

```http
POST /api/v1/search/keyword
Content-Type: application/json

{"repository_id":"<uuid>","query":"OrderService","top_k":5}
```

HTTP 200 returns `repository_id` and a `results` array. Each result has `chunk_id`, repository-relative `file_path`, `symbol_name`, one-based `start_line` and `end_line`, `match_type`, and `score`. `top_k` defaults to 5 and must be 1–20. Queries are trimmed, must contain non-whitespace text, and cannot exceed 2,000 characters. A valid query with no matches returns `{"repository_id":"<uuid>","results":[]}`. Unknown repositories return 404; invalid input receives the shared `VALIDATION_ERROR` 422 response; missing or unavailable cloud storage returns a controlled 503.

An exact, case-sensitive equality match on `symbol_name` or `qualified_name` has `match_type=EXACT_SYMBOL` and a defined score of `1.0`. An exact equality match on `parent_symbol` has `match_type=EXACT_PARENT_SYMBOL` and a defined score of `0.9`; this helps a class-name query find method chunks in a class that has no separate class chunk. Remaining matches use `match_type=FULL_TEXT` and PostgreSQL's calculated `ts_rank_cd` score. The three groups are ordered in that priority order, then by descending score, relative file path, source start byte, and chunk ID. Score values from the different groups are not calibrated probabilities and should not be compared across groups as one continuous relevance scale.

The full-text query uses PostgreSQL `websearch_to_tsquery` with the `simple` dictionary and a bound query parameter. Full-text matching is case-insensitive and token-based, while exact Java symbol equality is case-sensitive. The SQL always filters by repository UUID before returning results and applies a five-second statement timeout. Search input is never interpolated into SQL syntax. The response never exposes absolute source paths, database credentials, or other repositories' chunks.

## Verification

Unit tests inspect SQL construction and use controlled rows for response behavior. An opt-in integration test exercises exact class and method matches, qualified names, full-text ranking, case behavior, no matches, and repository isolation against an isolated managed test database at revision `20261030_0004`. Set `ARCHMIND_RUN_REMOTE_DB_TESTS=1` and `ARCHMIND_TEST_DATABASE_URL` to a dedicated migrated database whose name starts with `test_` or ends with `_test`. Do not use a production database.

In Swagger at `/docs`, execute the keyword endpoint with `OrderService`, `processPayment`, `payment gateway`, and a term absent from the indexed repository. The first three depend on those symbols and words existing in the indexed source; the last should return HTTP 200 with an empty array.

## Limits

PostgreSQL's `simple` parser does not split every camel-case or snake-case identifier into natural-language words. Exact matching requires the whole symbol or qualified name with matching case. A class with methods is represented by its method chunks, so an exact class-name hit may appear as `EXACT_PARENT_SYMBOL`. Keyword search cannot infer conceptual similarity when terms differ; semantic search can suggest such candidates, but semantic similarity alone cannot verify exact names or code behavior. Phase 5.1 itself does not fuse scores.

## Hybrid retrieval (Phase 5.2)

```http
POST /api/v1/search/hybrid
Content-Type: application/json

{"repository_id":"<uuid>","query":"How are orders processed?","top_k":5}
```

HTTP 200 returns `repository_id`, the trimmed `query`, and `results`. Each result contains `chunk_id`, repository-relative `file_path`, `qualified_name`, `rrf_score`, `retrieval_sources` (`semantic`, `keyword`), and `source_ranks` with one-based positions in each contributing list. The same query and `top_k` validation as keyword and semantic search applies. `top_k` defaults to 5 and is bounded to 1–20.

The hybrid service calls the existing semantic and keyword Python services with a bounded candidate pool, without calling their HTTP endpoints. `HYBRID_CANDIDATE_POOL` defaults to 20 and is configurable from 1–20; the service always requests at least `top_k`. `HYBRID_RRF_CONSTANT` defaults to 60 and is configurable from 1–1,000. For each unique `chunk_id`, RRF adds `1 / (constant + rank)` for every list containing that chunk. Rank starts at one. For example, a chunk ranked first by both lists scores `2 / 61` with the default constant. Cosine similarity and PostgreSQL `ts_rank_cd` remain separate and are never added together. Results sort by descending RRF score, then file path, qualified name, and chunk ID for stable ties.

After both strategies succeed, a repository-scoped database lookup supplies the authoritative file path and qualified name for the candidate IDs. If a returned ID is absent from that repository, hybrid search fails with a controlled 503 rather than returning a possibly stale or cross-repository result. If either strategy fails, the endpoint returns that documented error; it does not present the other strategy's results as a complete hybrid response. In particular, missing compatible semantic embeddings cause 409 even if keyword matches exist. Unknown repositories return 404; provider failures use 429, 502, or 504; database or configuration failures use 503. A successful pair of empty result lists returns HTTP 200 with `results: []`.

Hybrid search needs both the managed database and configured Gemini query embeddings. No new tables or migration are needed after `20261030_0004`. In Swagger at `/docs`, run the same question through `/api/v1/search/semantic`, `/api/v1/search/keyword`, and `/api/v1/search/hybrid`; compare candidate IDs, one-based source ranks, and the RRF formula. The tests do this with controlled service results. Live relevance and ordering remain dependent on stored chunks, embeddings, and provider output.

The 20-candidate cap can omit a useful item ranked lower by both strategies. RRF ranks evidence locations; it does not validate code behavior. Phase 5.2 has no reranking, generated answer, or LangGraph workflow.

## Metadata heuristic reranking (Phase 5.3)

```http
POST /api/v1/search/rerank
Content-Type: application/json

{"repository_id":"<uuid>","query":"Where is payment processing implemented?","top_k":5}
```

HTTP 200 returns `repository_id`, `reranker: "metadata_heuristic_v1"`, and `results`. Each result contains `chunk_id`, repository-relative `file_path`, one-based `original_rank` from hybrid retrieval, one-based `rerankerank` after reranking, calculated `rerank_score`, and `reranker_version`. The field name `rerankerank` follows this API contract. The request has the same query and `top_k` limits as hybrid search. All hybrid errors propagate; a metadata lookup failure or candidate that no longer belongs to the requested repository returns a controlled 503. An empty hybrid candidate list returns HTTP 200 with an empty result list.

The endpoint asks hybrid retrieval for a bounded pool of at least `top_k`, using the existing `HYBRID_CANDIDATE_POOL` setting (default 20, maximum 20). A repository-scoped lookup then reads `symbol_name`, `qualified_name`, and `contextual_header` for those chunk IDs. It verifies that each ID, path, and qualified name still matches the hybrid result before scoring. No repository source file is opened and no model is downloaded.

`metadata_heuristic_v1` uses these fixed signals:

| Signal | Contribution |
| --- | ---: |
| Exact, case-sensitive whole identifier equal to `symbol_name` in the query | 10 |
| Exact, case-sensitive whole identifier equal to `qualified_name` in the query | 5 |
| Query-term overlap with `symbol_name`, `qualified_name`, and relative file path | `2 × matching distinct terms / distinct query terms` |
| Query-term overlap with `contextual_header` | `matching distinct terms / distinct query terms` |
| Original hybrid rank | `1 / original_rank` |

Terms are lowercased and split on punctuation, underscores, and camel-case boundaries. An empty term set uses denominator 1. The five contributions are added in the displayed order; the score is a deterministic heuristic value, not a probability. The exact-symbol contribution exceeds the maximum possible sum of all other signals, so an exact symbol match always outranks a candidate without one. Results sort by descending score, then original rank, file path, and chunk ID for stable ties. `reranker_version` identifies the rule set for reproducibility.

Reranking changes the order of retrieved candidates; it cannot find a chunk omitted from the hybrid pool. Compare `/api/v1/search/hybrid` and `/api/v1/search/rerank` with the same question in `/docs` to inspect old and new ranks and recalculate the heuristic score. The unit tests exercise this comparison with controlled results. Live behavior still requires indexed cloud chunks, compatible embeddings, a configured Gemini query provider, and a working managed PostgreSQL connection. A learned reranker might use richer context but would add model or service costs; none is included here. There is no generative answer or LangGraph workflow.
