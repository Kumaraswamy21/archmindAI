# Java/Spring evaluation, 2026-11-03

These are measured results from one live run against the tracked synthetic Spring sample, an isolated schema on managed PostgreSQL, and the configured Gemini services. The runner completed indexing and both evaluation endpoints, then removed the schema. The raw sanitized response is in ignored `.local-docs/phase-07/evaluation-report.json` on the machine that ran the evaluation.

## Retrieval comparison

Dataset: `java-spring-search-v1` version `1.0.0`, eight questions, top 5. Snapshot: `sha256:ac782ebbcb61c49c891fe6faa76e163a44d80fa51e187f7a67e1a8c51c2308b8`. Measured at 2026-11-03 12:19:36 UTC.

| Strategy | Recall@5 | MRR | Mean latency (ms) |
| --- | ---: | ---: | ---: |
| Semantic | 0.96875 | 0.9375 | 3,937.97 |
| PostgreSQL full-text keyword | 0.00000 | 0.0000 | 1,891.43 |
| Hybrid RRF | 0.96875 | 0.9375 | 7,075.87 |
| Hybrid RRF + reranking | 0.96875 | 0.9375 | 8,701.49 |

Keyword search found no labeled symbols for these sentence-form questions. Hybrid and reranking did not improve Recall@5 or MRR over semantic on this sample and added latency. This is a regression in latency for those strategies on this run; it does not establish their behavior on other repositories. Keyword search uses PostgreSQL full-text ranking, not BM25.

## Agent, grounding, and reliability

Dataset: `java-spring-agent-v1` version `1.0.0`, eight questions on the same snapshot. Measured at 2026-11-03 12:22:55 UTC.

| Measure | Result |
| --- | ---: |
| Intent accuracy | 0.75 |
| Routing accuracy | 0.75 |
| Workflow completion rate | 0.75 |
| Workflow failure rate | 0.25 |
| Mean end-to-end latency | 24,397.27 ms |
| Citation validity | 1.00 |
| Claim support rate | 0.72414 |
| Unsupported claim rate | 0.27586 |
| Correct abstention | 0.00 |

The six answerable questions completed and matched their labeled intent and route. Both insufficient-evidence cases failed with `UNSUPPORTED_CITATION`; neither produced a valid abstention. The architecture question had six unsupported claims by the existing verifier, versus one supported claim. Citation validity means that the citation points to matching evidence in this repository; it does not establish that every sentence of the answer is correct. Claim support is based on the existing deterministic claim verifier, without an LLM judge.

## Reproduction record

- Embedding model: `gemini-embedding-2`.
- Generation model: `gemini-3-flash-preview`.
- Reranker: `metadata_heuristic_v1`.
- Classifier: `intent_supervisor_v1`.
- Agent retrieval: `top_k=5`, RRF constant `60`, candidate pool `20`.
- Runtime prompts: `rag_answer` and `query_classifier` system/user `1.0.0`; `code_analysis`, `dependency_analysis`, and `architecture_analysis` system/user `1.1.0`.
- Repository content: `backend/evaluation/spring_sample_repository`, pinned by the snapshot above.

This is one run on a small synthetic fixture. Latencies include remote database and provider calls and can vary. The Phase 5 dataset uses a different repository, so its scores are not used as a baseline here. There is no fabricated improvement percentage or claim of statistical significance.
