# ArchMind frontend

The Next.js App Router frontend provides a responsive dashboard, project directory, project registration form, and repository workspace shell. It uses React, strict TypeScript, Tailwind CSS 4, Lucide icons, and accessible native controls.

## Local setup

From `frontend/`:

```bash
npm install
cp .env.example .env.local
npm run dev
```

Start the FastAPI backend on `http://127.0.0.1:8000` and configure its approved repository aliases. Open `http://127.0.0.1:3000`. `ARCHMIND_API_URL` changes the server-side proxy target when the backend runs elsewhere. The browser calls only same-origin `/api/v1` routes. Keep `DATABASE_URL` and `GEMINI_API_KEY` in the backend environment; never use a `NEXT_PUBLIC_` prefix for them.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Dashboard and recent projects |
| `/projects` | Complete project directory, global search, index filters, and Open Project links |
| `/projects/new` | Register one server-approved source alias |
| `/projects/[repositoryId]` | Project workspace overview |
| `/projects/[repositoryId]/ai` | Streaming repository chat, agent activity, and source citations, gated by a ready index |
| `/projects/[repositoryId]/explore` | Read-only file tree, search, and source viewer |
| `/projects/[repositoryId]/dependencies` | Interactive static dependency graph |

The projects page follows the backend's paginated listing until all registered projects are loaded, then searches names, aliases, and IDs across the full directory. Cards show registration metadata and live vector index coverage. The registration form loads server-approved aliases, validates the display name, handles duplicate and API errors, and opens the new project after a successful POST. An unavailable backend appears as a retryable error state, with no fabricated project data.

The project overview starts or retries a durable indexing job, displays the eight pipeline stages, polls active jobs every three seconds, and recovers the latest persisted job after a page reload. Source-file, chunk, embedding, and vector coverage counts come from backend endpoints. AI analysis is enabled only when the active index reports `READY`. The [chat workspace](../docs/frontend/chat-workspace.md) uses fetch-based SSE for live LangGraph activity and opens citations in a resizable [source explorer](../docs/frontend/source-explorer.md). See [indexing lifecycle](../docs/frontend/indexing-workspace.md).

The [dependency explorer](../docs/frontend/dependency-explorer.md) uses React Flow to display persisted static relationships. It limits the graph to 100 relationships and 70 symbols, supports filters and exact-symbol focus, and opens relationship source lines in the read-only explorer.

## Checks

```bash
npm run api:types  # regenerate types from ../docs/api/openapi.json
npm run lint
npm run typecheck
npm run test
npm run build
```

`npm run test:browser` runs optional Chromium navigation checks against a running frontend at port 3000. Install the matching Playwright browser with `npx playwright install chromium`, or set `ARCHMIND_CHROMIUM_EXECUTABLE` to an existing Chromium executable. The build uses Webpack because the current environment denied Turbopack's CSS worker local port binding.

See [architecture notes](../docs/frontend/architecture.md) for the API boundary and component layout.
See [project management](../docs/frontend/project-management.md) for project behavior and status meanings.
