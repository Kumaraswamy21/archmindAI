# Local end-to-end validation (Phase 7.7)

Validated on 5 November 2026 against the existing local Next.js and FastAPI processes at `127.0.0.1:3000` and `127.0.0.1:8000`. The backend used the configured managed PostgreSQL database and Gemini key. No local database was started.

## Commands

From `backend`, load the ignored `.env` into the shell, then run `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`. Run `.venv/bin/python -m app.modules.project_indexing.worker` separately for queued indexing. From `frontend`, run `npm run dev`. Check `/api/v1/health`, `/api/v1/ready`, `/docs`, and the frontend root before testing.

The validation commands were `.venv/bin/python -m pytest -q`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, and `npm run test:browser`.

## Observed results

- Health and readiness returned HTTP 200; readiness checked the managed database, migration head, pgvector extension, and provider configuration.
- Cursor's embedded browser loaded the dashboard and its 13 project cards. The existing `Local UI Check` test project opened. Its completed index showed 1 source file, 12 chunks, and 12/12 embeddings. The persisted job displayed discovery, parsing, chunking, persistence, embedding, vector coverage, and dependency extraction as complete. These are job records, not a newly run indexing job in this validation.
- The source explorer loaded `src/main/java/example/CommerceService.java` with syntax coloring, line numbers, and read-only presentation. The dependency explorer loaded 12 `CALLS` relationships from that test project.
- A live browser question, “What does CommerceService.processPayment do?”, streamed workflow start, `CODE_EXPLANATION` classification, Code Analysis start/completion, evidence verification, and final answer. The response was `ANSWERED` / `CITATION_VERIFIED` with one citation. Clicking it opened the source and highlighted lines 4–6.
- Direct live agent request `ccca3830-f126-4a5f-8211-471988177f0d` located `CommerceService` with the Code Analysis route, five citations, `PARTIALLY_VERIFIED`, prompt version 1.1.0, and `gemini-3-flash-preview`. Request `3ab89661-0a3f-4fab-9fb5-ad95aaf3bd26` took the architecture route (code, dependency, and architecture agents) and returned `INSUFFICIENT_EVIDENCE` with no citations. A dependency question timed out at the client after 120 seconds; health and readiness remained HTTP 200 afterward.
- Backend: 336 passed, 8 skipped. Frontend: ESLint and TypeScript passed; Vitest 46 passed; production build passed.
- The initial Playwright run had 9 launch failures because its bundled Chromium executable was absent. Those are infrastructure failures, not browser assertion failures. The live approved-alias E2E path also requires `ARCHMIND_E2E_ALIAS`; the running backend returned an empty alias list.

## Defect found

An insufficient-evidence AI question returned HTTP 502 `UNSUPPORTED_CITATION` (request `81f3edc7-d44d-46b4-b59c-6b982fec6ff5`). The model reported `INSUFFICIENT` confidence while including citations. The RAG and specialist handlers now discard those citations, preserve an explicit limitation, and return the standard insufficient-evidence response. Focused RAG, specialist, workflow, and observability regression tests passed (60 tests); the full backend suite passed 338 with 8 opt-in skips. A separate corrected local API instance on port 8001 was used for live retesting so the existing port 8000 process could continue serving the UI. That retest returned HTTP 429 `CLASSIFIER_PROVIDER_FAILED` before generation (request `0b332e7a-162d-4809-a7fa-1701cccc1e20`), so the fix has no successful live retest yet. Port 8001 was stopped after the attempt.

## Scope and limits

Do not use an arbitrary filesystem path for registration. Configure a dedicated alias under an approved root before testing the alias registration and fresh indexing journey. The existing uploaded test project was used for read-only browser inspection and AI questions. Authentication is not implemented in the current development profile, so sign-in and session checks do not apply. Langfuse dashboard validation requires a configured cloud project and credentials; see [Langfuse](../observability/langfuse.md).

Do not count a mocked Playwright response as proof of database, Gemini, or Langfuse operation. Capture response request IDs and compare them with server workflow records and Langfuse trace metadata before claiming trace correlation.
