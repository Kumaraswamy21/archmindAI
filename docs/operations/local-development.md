# Local development

The backend uses Python and the existing `.venv`; the frontend uses npm and the existing `node_modules`. Keep secrets in the ignored `backend/.env`. Load it into the backend and worker process environment without printing its contents. Start the API with `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, the worker with `.venv/bin/python -m app.modules.project_indexing.worker`, and the UI with `npm run dev` from `frontend`.

Use only the configured remote PostgreSQL URL. `/api/v1/ready` verifies connectivity, current migration revision, pgvector, and provider configuration. A healthy `/api/v1/health` alone does not establish dependency readiness. Swagger is at `http://127.0.0.1:8000/docs`.

For approved-source onboarding, set `ARCHMIND_APPROVED_REPOSITORY_ROOTS` and `ARCHMIND_REPOSITORY_ALIASES` to administrator-approved locations. An empty `GET /api/v1/repositories/approved-aliases` response means the alias flow cannot be run. Use a designated test alias and avoid reindexing or editing existing project records.

If port 3000 or 8000 is occupied, inspect the listener and reuse the existing project process when appropriate. Playwright can use an installed Chrome executable through `ARCHMIND_CHROMIUM_EXECUTABLE`; its bundled browser must otherwise be installed. See [local validation](../testing/local-e2e-validation.md) for the executed checks.
