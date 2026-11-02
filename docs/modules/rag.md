# Grounded repository question answering

Phase 5.4 adds `POST /api/v1/ask`. It uses existing hybrid retrieval and metadata reranking to find relevant Java code chunks, builds a bounded evidence context, and asks Gemini for a structured answer. The backend verifies each citation against the exact source evidence supplied to Gemini. It does not modify or execute repository code and does not use LangGraph.

## Setup

Configure a managed PostgreSQL database migrated through revision `20261030_0004`, register and index approved Java source files, and generate compatible embeddings. Set `GEMINI_API_KEY` and the existing embedding settings so hybrid retrieval works. The RAG generation model is configurable with `RAG_MODEL_IDENTIFIER` (default `gemini-2.5-flash`); `RAG_TIMEOUT_SECONDS` defaults to 30 and is bounded to 120; `RAG_MAX_CONTEXT_CHARS` defaults to 16,000 and is bounded to 100,000. The optional `RAG_SYSTEM_PROMPT_VERSION` and `RAG_USER_PROMPT_VERSION` select released prompt versions independently. Missing configuration returns controlled errors. No local database or model download is used.

The versioned runtime templates and active registry are described in [prompt management](../prompt-management.md). The initial releases are system `1.0.0` and user `1.0.0`. The question and source context are substituted at request time, so neither belongs in Git-tracked templates. The API returns prompt name, both selected versions, and the generation model identifier for every answer, including insufficient-evidence responses. Gemini receives a system instruction and a separate user message, with a Pydantic structured-output schema and zero temperature.

## API

```http
POST /api/v1/ask
Content-Type: application/json

{"repository_id":"<uuid>","question":"How does OrderService create an order?","top_k":5}
```

The question is trimmed, must contain text, and has a 2,000-character maximum. `top_k` defaults to 5 and must be 1–20. A successful response includes `repository_id`, `status` (`ANSWERED` or `INSUFFICIENT_EVIDENCE`), `answer`, `citations`, `confidence` (`HIGH`, `MEDIUM`, `LOW`, or `INSUFFICIENT`), `limitations`, `excluded_evidence_ids`, `prompt_versions`, and `model_identifier`. Confidence is a label, not an invented numeric probability. An insufficient-evidence response is explicit, has no citations, and uses a standard answer sentence. Unknown repositories return 404; missing compatible embeddings return 409; invalid requests return the shared `VALIDATION_ERROR` 422 response; provider rate limits, invalid output/citations, unavailable storage/configuration, and timeouts return 429, 502, 503, and 504 respectively.

## Evidence and citation contract

The service asks the existing reranker for up to `top_k` candidates and loads their source text from repository-scoped persisted chunks. Each context record contains evidence ID, repository UUID, repository-relative file path, qualified symbol, one-based source line span, and exact chunk content. Records are JSON serialized so source text is clearly data. Fully contained byte spans in the same file are deduplicated; partially overlapping spans remain. Whole records that exceed the character budget are omitted and listed in `excluded_evidence_ids` and `limitations`. Source text is never silently cut. If no evidence fits, Gemini is not called.

Gemini must cite an evidence ID actually supplied in the context. The server rejects a citation if its ID is absent, its path differs, or its line range falls outside the cited source span. A non-insufficient answer without citations is rejected. If Gemini reports insufficient evidence, the API returns the standard insufficient-evidence response and no citations. The server validates citation structure and span membership; it cannot prove that every generated claim is semantically entailed by cited code. Source contents remain untrusted even when cited, and source annotations do not prove runtime behavior.

## Verification and limits

Unit tests mock Gemini and cover prompt loading and rollback, exact runtime rendering, prompt integrity, structured output, source-instruction injection, bounded context, citation checks, insufficient evidence, provider timeouts, repository isolation, and OpenAPI contracts. In `/docs`, try a question about an indexed method, a question without supporting source, an invalid request, and a controlled provider failure. Compare returned citations with actual file paths and line ranges; inspect prompt versions and limitations. Live answers require a configured managed database and Gemini credentials.

RAG can miss relevant code if retrieval or reranking omits it, and a model can still make a claim that a structurally valid citation does not truly support. This initial workflow has no independent claim verifier, LangGraph graph, multi-agent process, or automatic repository modification. Evaluate answers and citations before using them for high-stakes architecture decisions.
