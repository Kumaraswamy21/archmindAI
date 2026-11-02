# Project management interface

## Directory

`/projects` loads every registered project from `GET /api/v1/repositories` in pages of up to 100. The directory searches project names, approved aliases, and repository IDs across the complete result set. An indexing filter groups the backend's vector coverage states: Ready, In progress (`PENDING` or `PARTIAL`), Not indexed (`EMPTY`), and Needs attention (`FAILED` or status unavailable).

Each card shows the persisted display name, alias, registration date, short repository ID, registration state, current index state, and an Open Project link. Index state is read from `GET /api/v1/repositories/{repository_id}/index/status`. If that API is unavailable, the card says “Index unavailable”; it does not imply the repository is unindexed. The workspace overview shows the live pipeline. The dashboard preview uses the same real project cards for its first three entries.

## Add project

`/projects/new` fetches `GET /api/v1/repositories/approved-aliases`. Users choose one returned alias and enter a display name. The form trims the name, requires printable text, and limits it to 100 characters. The alias must be one of the server-returned options. The browser sends `POST /api/v1/repositories` with `{name, source_alias}` and navigates to `/projects/{repository_id}` after HTTP 201.

The backend remains authoritative for duplicate names and aliases. A 409 `REPOSITORY_ALREADY_REGISTERED` response produces a specific message; other backend errors show their public message and request ID. The interface never accepts or sends a filesystem path or Git URL. Loading, empty, and API failure states are explicit and retryable where appropriate.

The backend's `index/status` route reports persisted vector coverage, not the current durable indexing job stage. See [the workspace indexing lifecycle](indexing-workspace.md) for job progress and recovery.
