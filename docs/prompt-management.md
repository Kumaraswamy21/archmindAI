# Runtime LLM prompt versioning standard

Every application system prompt and reusable user prompt template used by RAG, future LangGraph, or future multi-agent modules is stored as a Git-tracked file outside Python business logic. The first LLM-powered module, Phase 5.4 RAG, introduces the shared loader and central registry. The developer's actual question and retrieved repository code are runtime values, never new prompt releases.

## Released files and registry

Templates live at `backend/app/prompts/templates/<prompt-name>/vMAJOR.MINOR.PATCH/system.md` and `user.md`. A version directory may contain one or both template kinds. The central `backend/app/prompts/registry.toml` lists every released system and user template independently, records its SHA-256 digest and required variables, and selects an active version for each kind. The loader verifies the digest and variable list before use. Released versions must never be overwritten or deleted. A change requires a new semantic version, changelog entry, and regression evaluation before production promotion.

- **MAJOR:** incompatible variable, output, or behavior change.
- **MINOR:** backward-compatible capability or behavior addition.
- **PATCH:** correction that preserves the contract.

Phase 5.4 releases `rag_answer` system `1.0.0` and user `1.0.0`. The system prompt contains trusted grounding and source-safety instructions. The user template declares `${repository_id}`, `${question}`, and `${evidence_context}`; the values are supplied only at runtime. No API keys, credentials, or private repository contents belong in released templates.

Phase 6.2 releases `query_classifier` system `1.0.0` and user `1.0.0`. Its user template accepts only the runtime question. Deterministic rules handle clear intents without loading or calling the classifier prompt. Ambiguous questions load the released classifier versions and use structured Gemini output. The selected prompt name, versions, and model identifier appear in workflow metadata only when the LLM classifier runs.

Phase 6.4 releases independent `code_analysis`, `dependency_analysis`, and `architecture_analysis` system and user templates at 1.0.0. Each specialist records its selected prompt name, system version, user version, and model identifier in workflow metadata. The Phase 5 `rag_answer` release remains unchanged. Optional environment variables named `<PROMPT_NAME>_SYSTEM_PROMPT_VERSION` and `<PROMPT_NAME>_USER_PROMPT_VERSION` select an earlier released version for rollback.

Phase 6.5 releases specialist system and user versions 1.1.0, requiring inline evidence IDs for each generated claim. The central registry selects 1.1.0 by default; 1.0.0 files and digests remain available for rollback. Deterministic evidence verification introduces no LLM prompt. Mocked claim-grounding regression fixtures are run before selecting the new active versions; live answer-quality evaluation remains a deployment gate when cloud services are configured.

## Selection, rollback, and validation

`PromptLoader().load("rag_answer")` selects the active system and user versions from the registry. Callers can choose either independently with `system_version=` and `user_version=`. The RAG service reads optional `RAG_SYSTEM_PROMPT_VERSION` and `RAG_USER_PROMPT_VERSION` environment variables for explicit selection. A rollback points one or both variables, or the registry active pointers, to previously released versions; it never edits a released file. Missing prompt names, invalid or missing versions, altered template hashes, invalid placeholders, and missing render variables fail clearly.

Promote a new version only after reviewing the file, adding the registry release entry and changelog, and running prompt and answer regression evaluations. The SHA-256 check detects accidental edits to a released template; Git review and repository policy enforce the no-overwrite rule for registry changes. Keep experiments and unapproved drafts under ignored `.local-docs/` until safe to release.

## Execution metadata and observability

Every LLM answer records prompt name, selected system version, selected user version, and model identifier in its response metadata. When Langfuse is enabled, the same fields are attached to traces and generations. See [Langfuse observability](observability/langfuse.md). Tracing stays optional and never receives prompt bodies, source text, or secrets.

## Source data and local notes

Source-code evidence is untrusted data. The RAG system prompt instructs Gemini to ignore instructions embedded in it, and the application checks generated citations against the evidence supplied. Prompt text, registry, this policy, README guidance, and [the prompt changelog](prompt-changelog.md) are tracked. Personal explanations, experimental notes, and sensitive drafts belong in `.local-docs/`, which is Git-ignored.
