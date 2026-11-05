# RAG and agent evaluation

Phase 7.6 extends the Phase 5 retrieval evaluator. It keeps the original `java-code-search-v1` dataset and metrics intact. The new `java-spring-search-v1` and `java-spring-agent-v1` datasets pin a tracked synthetic Spring repository to a content snapshot. They are versioned independently; change labels or source by publishing a new dataset version and snapshot, rather than rewriting released versions.

## Dataset and scope

The sample source is `backend/evaluation/spring_sample_repository`. Retrieval labels identify qualified Java symbols for eight questions. Agent labels cover code lookup, method explanation, dependency analysis, architecture analysis, change impact, and two insufficient-evidence cases. The intent and route labels describe the expected classifier behavior. Only labels established by inspection of the tracked sample are included. The fixture is small and synthetic, so results do not predict performance on a large production repository.

`repository_snapshot_id` is a SHA-256 digest over the indexed chunk manifest. An evaluation refuses a mismatched snapshot and checks again after execution. The same snapshot is required for meaningful comparisons. Neither endpoint returns source text or generated answers.

## Scores

Retrieval runs each question through semantic vector search, PostgreSQL full-text keyword search, hybrid reciprocal rank fusion (RRF), and hybrid RRF with reranking. PostgreSQL full-text ranking is not BM25. For each query, Recall@5 is the fraction of labeled symbols appearing in the first five results. MRR is the reciprocal rank of the first relevant symbol, or zero if none appears. Both are averaged over queries. Latency is the average wall-clock search time in milliseconds, including provider calls.

The agent evaluator reports:

| Score | Definition |
| --- | --- |
| Intent accuracy | Expected intent matches classified intent, divided by all questions. |
| Routing accuracy | Expected workflow matches selected workflow, divided by all questions. |
| Completion / failure rate | Completed or failed question runs, divided by all questions. |
| End-to-end latency | Mean elapsed time per question, including failed attempts. |
| Citation validity | Citations whose evidence ID, repository, path, and line span match returned evidence, divided by all citations. |
| Claim support / unsupported claim rate | Verified or unsupported claims, divided by their combined count. |
| Correct abstention | Insufficient-evidence questions completed with `INSUFFICIENT_EVIDENCE` and no citation, divided by all such questions. |

Rates with no applicable denominator are `null`, not zero. Failed runs count against intent/routing accuracy and completion. Citation validity and claim support are deterministic structural checks based on the existing verifier. They cannot prove semantic truth or detect every unsupported statement. No LLM judge is used.

## Reproduce

Install backend dependencies using the existing Python environment. Run `pytest -q` from `backend`. For a live run, configure an **isolated test** managed PostgreSQL connection in `backend/.env` and a Gemini key. Then run from `backend`:

```sh
set -a
source .env
ARCHMIND_RUN_EVALUATION_REMOTE=1 ./.venv/bin/python evaluation/run_rag_agent_eval.py
```

The opt-in runner creates a uniquely named schema on the configured remote database, applies migrations inside it, registers and indexes the tracked sample, calls both evaluation APIs, then drops the schema. It refuses to proceed without both credentials. Use a test database whose account can create and drop schemas; never point this command at production. The measured JSON report is written to ignored `.local-docs/phase-07/evaluation-report.json`. A failed run reports only the exception class so connection strings and provider payloads do not appear in logs.

The agent response records dataset/version, repository snapshot, embedding and generation models, retrieval configuration, reranker and classifier versions, runtime prompt versions, and UTC evaluation time. The retrieval response records its model, reranker, dataset, snapshot, and time. For reproducibility, preserve both responses and the repository commit or source archive separately. The repository UUID changes on each run, while the content snapshot remains comparable.

Langfuse receives evaluation run/question IDs and aggregate or structural metadata through the existing sanitized tracer when configured. Questions are hashed by the tracing layer; source code, raw prompts, generated answers, and secrets are not sent as evaluation metadata. Provider calls may still have their own telemetry behavior outside this evaluator.

## Reading results

Compare retrieval strategies within one report on the same snapshot. Lower Recall@5 or MRR and higher latency are regressions for that sample. Inspect per-question agent status and error codes when completion is below one. Do not compare these new scores directly with the Phase 5 dataset: it uses a different repository and questions. The report includes actual measurements only; network, model, and service variability can change latency and generated answers between runs.
