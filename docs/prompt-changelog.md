# Prompt changelog

## 2026-10-31 — specialist claim-link releases 1.1.0

- **`code_analysis`, `dependency_analysis`, `architecture_analysis` system/user 1.1.0:** require one substantive claim per line with inline evidence IDs, matching structured citations, and explicit observation/recommendation labels. Released 1.0.0 files remain unchanged.
- **Validation:** prompt loader integrity and variable tests, per-claim citation regression fixtures, partial correction, and agent workflow regression tests. Live Gemini answer-quality evaluation remains pending configured cloud services.
- No LLM evidence verifier was introduced, so no `evidence_verification` prompt release is needed.

## 2026-10-31 — specialized analysis prompts 1.0.0

- **`code_analysis`:** released system/user 1.0.0 for grounded class, method, and flow answers; runtime variables are repository ID, question, and bounded source evidence.
- **`dependency_analysis`:** released system/user 1.0.0 for persisted static edges, with explicit resolved versus unresolved treatment.
- **`architecture_analysis`:** released system/user 1.0.0 for source and edge synthesis that separates observed facts from proposed changes.
- **Validation:** loader digest and variable checks, mocked specialist answers, citation rejection, context bounds, and deterministic evidence aggregation. Live Gemini and cloud database verification require configured services.

## 2026-10-31 — query_classifier 1.0.0

- **System 1.0.0:** classify developer questions into the five Phase 6.2 intents without answering or inventing repository facts.
- **User 1.0.0:** runtime `question` variable only; no private source content is stored in the template.
- **Validation:** registry digest and variable checks, deterministic-rule tests, mocked Gemini structured output and failure tests. Live Gemini classification remains dependent on configured credentials.

## 2026-10-30 — `rag_answer` 1.0.0

- **System 1.0.0:** initial grounded-answer policy: use supplied repository evidence only, treat source as untrusted, cite source spans, distinguish inference, and state insufficient evidence.
- **User 1.0.0:** runtime placeholders for `repository_id`, `question`, and `evidence_context`; no question or source contents are committed in the template.
- **Validation:** loader, version selection, rendering, missing-variable, and integrity tests; mocked grounded-answer, citation, context-limit, and prompt-injection tests. Live answer evaluation remains pending cloud database and Gemini credentials.
