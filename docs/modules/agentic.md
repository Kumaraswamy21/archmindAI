# Agentic repository analysis (Phases 6.1–6.5)

`POST /api/v1/agents/analyze` accepts a repository UUID, a 1–2,000 character question, and optional `top_k` from 1–20. It runs a request-local LangGraph supervisor:

```text
START → classify_intent → one specialist → aggregate → verify → respond → END
                           code_analysis
                           dependency_analysis
                           architecture_analysis
                           clarify (UNKNOWN only)
```

The registered repository is checked before classification. Clear patterns use deterministic rules; ambiguous questions use Gemini structured output with the released `query_classifier` prompt. The response records intent, routing reason, classifier version, and classifier prompt/model metadata when Gemini was called. No graph checkpointer or persistent workflow state is configured.

## Specialists

| Intent | Specialist | Evidence and answer |
| --- | --- | --- |
| `CODE_LOOKUP`, `CODE_EXPLANATION` | `code_analysis` | Existing reranked retrieval and RAG evidence/citation validation with released `code_analysis` prompts |
| `DEPENDENCY_ANALYSIS` | `dependency_analysis` | Persisted static Java edges from the same repository; resolved and unresolved statuses remain explicit |
| `ARCHITECTURE_ANALYSIS` | `architecture_analysis` | Bounded code and dependency evidence gathered concurrently, then one grounded synthesis using released `architecture_analysis` prompts |
| `UNKNOWN` | clarification | No source search or model answer; asks for a more specific question |

The architecture specialist uses at most two worker threads for independent read-only evidence collection. It waits for both before synthesis and fails on either error; it never silently presents a partial result as complete. The evidence builder sorts records by repository-relative path, line span, kind, and stable key, removes exact duplicates, and caps the prompt at 40 records and 16,000 characters. Omissions appear in limitations. Dependency discovery inspects at most the first 200 persisted edges and reports that bound when the repository has more. This is a known recall limit for large graphs.

Each specialist response contains an answer, checked file-and-line citations, confidence label, limitations, and its selected system/user prompt versions and model identifier. Dependency and architecture specialists validate that every citation refers to included evidence and stays within its source span. Code analysis uses the existing RAG citation checks. Source text and relationship labels are untrusted data; released prompts contain no private repository content.

The API response includes `request_id`, `repository_id`, `answer`, `citations`, `workflow_type`, and `workflow_metadata`. Metadata includes `classified_intent`, `selected_workflow`, `agents_executed`, `evidence_count`, `execution_duration_ms`, completed nodes, answer status, confidence, limitations, and prompt/model versions. For architecture synthesis, `agents_executed` includes both evidence collectors and the architecture specialist. A request with no relevant evidence returns `INSUFFICIENT_EVIDENCE` without invented citations. Unknown intent returns `CLARIFICATION_REQUIRED`.

Phase 6.5 inserts `evidence_aggregation → evidence_verification` before the final response. A failed claim check follows one `correct_answer → verify_corrected` pass; there is no loop. The response adds `verification_status`, per-claim results, unsupported claims, and correction count. Specialist prompts 1.1.0 require inline evidence IDs, and the verifier reuses Phase 5 citation integrity checks. See the [evidence verification contract](evidence-verification.md) for what this stage proves and its semantic limits.

HTTP 404 means the repository is not registered. HTTP 409 covers missing compatible search embeddings for a code or architecture route. HTTP 429, 502, and 504 cover controlled Gemini errors; HTTP 503 covers missing configuration, database, or prompts. FastAPI returns HTTP 422 for invalid requests. The endpoint never writes to or executes repository code.

## Setup and limitations

Install backend dependencies, configure managed `DATABASE_URL` and `GEMINI_API_KEY`, run migrations explicitly, register a repository, index code chunks, generate embeddings, and run the Phase 6.3 dependency scan. Code and architecture routes require compatible retrieval embeddings. Dependency analysis reads persisted static edges; if none match the question, it reports insufficient evidence. Optional `CODE_ANALYSIS_*`, `DEPENDENCY_ANALYSIS_*`, and `ARCHITECTURE_ANALYSIS_*` prompt version environment variables select released system/user versions independently.

Static edges do not prove runtime behavior, transitive impact, Spring bean selection, or external library wiring. An answer based entirely on unresolved edges must explicitly state that uncertainty. Architecture answers must separate observed repository facts from proposed migration considerations; obvious claims that a proposed migration was completed are rejected. These phrase checks are narrow guards, not a substitute for evidence review. The existing Phase 5 `/api/v1/ask` endpoint keeps its released `rag_answer` prompts and behavior.
