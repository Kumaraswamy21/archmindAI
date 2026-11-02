# Frontend foundation architecture

## Application structure

`frontend/src/app` owns the App Router pages and loading, error, and not-found boundaries. `AppShell` provides the responsive sidebar, top navigation, theme toggle, skip link, and main landmark across routes. Project cards and feedback states are reusable components. The visual system uses dark-first CSS tokens in `globals.css`, a light theme override, Tailwind CSS 4, and Lucide icons.

The dashboard and project directory read live repository metadata. The directory loads all backend pages before applying search and index filters, so a project beyond the first page remains discoverable. Index coverage comes from each repository's `/index/status` endpoint, with bounded request concurrency; unavailable status is labeled as unavailable. Registration selects a server-approved alias and sends only a display name and alias. Project workspace routes keep the repository UUID in the URL. The source and dependency explorers use repository-scoped backend APIs.

## API boundary

The tracked backend [OpenAPI export](../api/openapi.json) generates `frontend/src/lib/api/schema.d.ts` with `npm run api:types`. `src/lib/api/client.ts` uses those generated response and request types for repository listing, details, approved aliases, and registration. The client handles the shared backend error envelope and abortable reads.

`next.config.ts` rewrites browser requests from `/api/v1/*` to `ARCHMIND_API_URL` on the Next.js server. This keeps the browser on one origin and leaves backend credentials outside the frontend bundle. The rewrite target defaults to `http://127.0.0.1:8000` for local development. Only backend-approved aliases can register repositories; the frontend never sends arbitrary filesystem paths.

The AI workspace uses `POST /chat/stream` through a fetch reader to display actual LangGraph events. It falls back to `POST /chat` only when the stream route is explicitly unavailable. Chat turns exist in React state for this page session; the backend treats `conversation_id` as correlation only and no prior turn is sent as memory. Source citations open a dialog that calls the repository-scoped file-content API for the cited line range.

## Accessibility and responsive behavior

The shell has a skip link, named navigation landmarks, current-page states, keyboard focus outlines, Escape dismissal for the mobile drawer, and labeled icon buttons. Loading and error states use live status and alert semantics. Layouts collapse from a desktop sidebar and multi-column cards to a mobile drawer and single-column content. Motion is reduced when the operating system requests it.

## Phase boundary

The frontend now includes managed project indexing, streaming chat, source navigation, and bounded dependency visualization. Each view reads the backend API through the same-origin proxy.
