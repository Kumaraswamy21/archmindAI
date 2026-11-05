# Manual rollback and recovery

Production rollback is not available yet because authentication and authorization are incomplete. These instructions apply after that blocker is resolved and a deployment is approved.

1. Stop new traffic and stop the indexing worker before changing code or database state. Let in-flight requests drain within the configured graceful-shutdown window. A provider call may finish after a client disconnect.
2. Identify the last known working backend and frontend builds and the current Alembic revision. Restore both application builds together when their API contracts changed. Keep the frontend `ARCHMIND_API_URL` pointed at the intended backend.
3. Do **not** run `alembic downgrade` as a default rollback. The pgvector migration explicitly disallows automatic downgrade, and later data may rely on new schema. If the prior code is compatible with the migrated schema, restart it and verify readiness and smoke checks. Otherwise restore a reviewed database snapshot to a separate managed database, validate data and extension state, and switch connection settings during a controlled outage.
4. Restore any uploaded source files from the corresponding persistent-volume backup. Database rows alone do not restore ZIP-uploaded source trees. An API and worker restart must see the same approved paths.
5. Restart the worker, backend, and frontend. Check `/api/v1/health`, `/api/v1/ready`, repository listing, source access, indexing, retrieval, and citations. Review indexing jobs with expired leases; a replacement worker can reclaim them. Request-bound LangGraph runs do not resume; stale runs become failed or cancelled after startup recovery.

Backups and restoration are operator procedures supplied by the managed PostgreSQL and storage providers. Test a restore in isolation before relying on it. Never restore production data into a public or shared test environment.
