# Static Java dependency intelligence (Phase 6.3)

The dependency scanner reads registered repositories through the existing secure file discovery and source reader. It parses each Java file once with the existing Tree-sitter adapter and symbol extractor, builds a repository-local declaration index, and emits only these relationships: `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `INJECTS`, and `CALLS`. It does not execute or modify repository code.

Each edge contains a stable SHA-256 `relationship_id`, repository UUID, source and target symbols, relationship type, repository-relative file path, one-based source line range, and `RESOLVED` or `UNRESOLVED`. An import's source is the compilation unit marker `file:<relative-path>`; inheritance and injection source the enclosing type; a call sources the enclosing method or constructor. `RESOLVED` means the target declaration was found unambiguously among files in the same scan. External libraries, missing declarations, wildcard/static imports, overload ambiguity, and dynamic dispatch are unresolved. The scanner keeps the lexical target on unresolved edges and never guesses a fully qualified name.

Field injection requires `@Autowired` or `@Inject`. Constructor injection requires one constructor on a Spring component or an explicitly annotated constructor. A direct method call resolves only when the receiver is the current type or a known field or parameter type, and exactly one matching method declaration exists. A local variable sharing a field name makes that receiver unresolved. Calls through arbitrary expressions, static type names, and runtime dispatch remain unresolved. Annotation presence is source metadata, not proof of Spring runtime behavior.

## API

`POST /api/v1/repositories/{repository_id}/dependencies/scan` scans all supported Java files in the registered repository and atomically replaces its persisted relationship snapshot. It returns HTTP 200 with `repository_id`, `status: COMPLETED`, and `total_relationships`. Duplicate scans produce the same identifiers and upsert rows idempotently. A Java syntax error returns HTTP 422 and leaves the prior snapshot intact. Repository discovery and source-reading limits still apply.

`GET /api/v1/repositories/{repository_id}/dependencies` returns `repository_id`, `total_relationships`, and a stable ordered `relationships` array. Optional query parameters:

| Parameter | Meaning |
| --- | --- |
| `symbol` | Exact source or target symbol; 1–500 characters |
| `relationship_type` | One of the five supported types |
| `direction` | `outgoing` (source), `incoming` (target), or `both` (default) |
| `limit` | 1–500; default 100 |

Filters are parameterized and always include the repository UUID. HTTP 404 means the repository is not in the persisted registry. HTTP 503 means the managed PostgreSQL database is not configured or available. FastAPI handles invalid query parameters with HTTP 422. Absolute local paths and database connection details are never returned.

## Persistence and migration

Apply Alembic revision `20261031_0005` explicitly to the managed PostgreSQL database:

```sh
cd backend
python -m alembic -c alembic.ini upgrade head
```

The `dependency_relationships` table has a repository foreign key, source/target/type indexes scoped by repository, line and enum checks, and a deterministic primary key. Each scan upserts the repository row and current edges, then deletes stale edges for that repository in one transaction. No graph database or local database is used. The API does not run migrations at startup.

Static extraction does not resolve classpaths, external JARs, polymorphic call targets, reflection, Spring bean selection, or runtime wiring. Large repositories are still scanned synchronously under the Phase 2 discovery and file-size limits; a later phase can introduce incremental analysis if needed.
