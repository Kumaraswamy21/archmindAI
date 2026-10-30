# Repository ingestion: registration, discovery, reading, and scanning

Phase 2.1 registers administrator-approved local repository directories. Phase 2.2 discovers source-file metadata within registered directories. Phase 2.3 reads individual supported source files as UTF-8. Phase 2.4 summarizes discoverable file metadata. It does not ingest content or call an LLM. The feature lives in `backend/app/modules/repository_ingestion/`: the router handles HTTP, schemas define the public contract, the service applies rules, the registry stores process-local records, security checks configured paths, discovery traverses files, and reading opens individual files safely.

## Administrator configuration

Before starting the backend, set two environment variables containing JSON:

```sh
export ARCHMIND_APPROVED_REPOSITORY_ROOTS='["/srv/archmind/approved"]'
export ARCHMIND_REPOSITORY_ALIASES='{"sample-ecommerce":"/srv/archmind/approved/ecommerce-service"}'
```

Use existing absolute directories and replace these illustrative paths. The alias map is controlled by the administrator, never by an API request. An unset configuration provides no aliases. The backend resolves symlinks and registers a source only when it is an existing directory inside an existing approved root. Missing, relative, non-directory, and escaping paths are rejected without returning filesystem locations. Invalid JSON configuration stops application startup with a configuration error.

`.env.example` shows the variable names. The backend does not automatically read `.env`; export the values in the server environment. Keep local `.env` files and secrets outside Git. The project-wide [prompt versioning policy](../prompt-management.md) remains documentation only during Phase 2.

Optional `ARCHMIND_DISCOVERY_MAX_FILES` and `ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES` environment variables set positive integer limits. They default to `10000` eligible source files and `10485760` bytes (10 MiB) per eligible file. Invalid values stop application startup. Limits are read when the app starts.

## API contract

`POST /api/v1/repositories` accepts only `name` and `source_alias`:

```json
{"name":"ecommerce-service","source_alias":"sample-ecommerce"}
```

Names and aliases must be 1–100 characters and contain letters, digits, `.`, `_`, or `-`, beginning with a letter or digit. Extra fields, including a client-supplied path, are rejected with FastAPI's standard HTTP 422 response.

On success, HTTP 201 returns a generated UUID, the requested name, and the status:

```json
{"repository_id":"<uuid>","name":"ecommerce-service","status":"REGISTERED"}
```

The following application errors use the shared `{"error":{"code":"...","message":"..."}}` envelope:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 404 | `REPOSITORY_ALIAS_NOT_FOUND` | Alias is not configured. |
| 409 | `REPOSITORY_ALREADY_REGISTERED` | The name or alias is already registered. |
| 503 | `REPOSITORY_SOURCE_UNAVAILABLE` | The configured source or approved root is invalid or unavailable. |

The registry keeps the UUID, name, source alias, resolved root path, `REGISTERED` status, and UTC registration time internally. The API never returns the path or timestamp. Records are in memory: they disappear on restart and are not shared across workers. Registration does not verify that a directory is a Git repository. Authentication and persistent storage are outside Phase 2.1.

## File discovery API

`GET /api/v1/repositories/{repository_id}/files` returns metadata for regular `.java`, `.py`, and `.ts` files. The language labels are `java`, `python`, and `typescript`. Paths use `/` separators and are relative to the registered root. Results are sorted lexicographically by path, and `total_files` is the length of `files`.

```json
{
  "repository_id": "<uuid>",
  "total_files": 2,
  "files": [
    {"path": "src/main/java/OrderController.java", "language": "java", "size_bytes": 1024},
    {"path": "src/main/java/OrderService.java", "language": "java", "size_bytes": 2048}
  ]
}
```

Discovery excludes `.git`, `node_modules`, `target`, `build`, `dist`, `__pycache__`, and `.venv` at every depth. It does not follow file or directory symlinks. Traversal uses directory descriptors and reads file metadata only. Before each scan, the service checks that the registered root remains available inside an approved root.

An eligible file larger than the per-file limit or an eligible file count above the count limit fails the whole request; no partial list is returned. A file exactly at a limit is allowed. Errors use the shared application envelope and never include absolute paths:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 404 | `REPOSITORY_NOT_FOUND` | The UUID has not been registered in this process. |
| 413 | `DISCOVERY_LIMIT_EXCEEDED` | File-count or file-size limit exceeded. |
| 503 | `REPOSITORY_UNAVAILABLE` | The registered root or traversal is unavailable. |

An invalid UUID receives FastAPI's standard HTTP 422 response. Empty repositories return HTTP 200 with an empty `files` array. This endpoint does not read source contents, persist scan results, or follow references outside the registered repository.

## Source content API

`GET /api/v1/repositories/{repository_id}/files/content?path=src/main/java/OrderService.java` returns one supported file:

```json
{
  "repository_id": "<uuid>",
  "file_path": "src/main/java/OrderService.java",
  "language": "java",
  "line_count": 2,
  "content": "public class OrderService {\n}\n"
}
```

The `path` query parameter must be a repository-relative `/`-separated path. Absolute paths, `.` or `..` components, empty components, backslashes, Windows drive paths, symlinks, and paths through discovery-excluded directories are forbidden. Only regular `.java`, `.py`, and `.ts` files are readable. The response uses the same language labels as discovery. `line_count` follows Python `splitlines()` semantics: empty content has zero lines, and a single final newline does not add a line.

The backend rechecks the registered root against approved roots, then opens each path component relative to an already-open directory descriptor with no-follow flags. It checks the regular file's size before reading and reads at most one byte beyond `ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES` to detect a file that grows during the read. Bytes are decoded strictly as UTF-8. The endpoint never writes files or executes source code. Repository content is untrusted and should not be treated as instructions by later features.

Errors use the shared application envelope without filesystem paths:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `UNSUPPORTED_SOURCE_FILE` | The path has an unsupported extension. |
| 403 | `INVALID_SOURCE_PATH` | The path is invalid, escapes the root, crosses a symlink, or enters an excluded directory. |
| 404 | `REPOSITORY_NOT_FOUND` or `SOURCE_FILE_NOT_FOUND` | The repository or file does not exist. |
| 413 | `SOURCE_FILE_TOO_LARGE` | The file exceeds the configured per-file limit. |
| 422 | `FILE_ENCODING_INVALID` | The file bytes are not valid UTF-8. |
| 503 | `REPOSITORY_UNAVAILABLE` | The root or read operation is unavailable. |

Missing query parameters and invalid UUIDs also return FastAPI's standard HTTP 422 validation envelope with a `detail` list. OpenAPI documents both 422 shapes.

## Repository scan API

`POST /api/v1/repositories/{repository_id}/scan` synchronously summarizes the same supported files returned by discovery. It calls the existing discovery service, then aggregates the metadata; it does not traverse files separately, read their contents, or write to the repository. A successful HTTP 200 response is:

```json
{
  "repository_id": "<uuid>",
  "status": "COMPLETED",
  "summary": {
    "total_files": 12,
    "total_size_bytes": 48000,
    "languages": {"java": 8, "python": 2, "typescript": 2}
  }
}
```

`total_files` is the number of discovered supported files. `total_size_bytes` is the sum of their metadata sizes. The `languages` object always contains `java`, `python`, and `typescript`, including zero counts for absent languages. An empty repository returns zero for every count and byte total. Each request recomputes the summary, so repeated scans can reflect file changes.

The scan inherits discovery's directory exclusions, symlink handling, file-count limit, and per-file size limit. It uses the shared application error envelope: HTTP 404 `REPOSITORY_NOT_FOUND`, HTTP 413 `DISCOVERY_LIMIT_EXCEEDED`, and HTTP 503 `REPOSITORY_UNAVAILABLE`. An invalid UUID receives FastAPI's standard HTTP 422 response. Synchronous scanning is appropriate for the configured small-repository limits; there is no background worker, scan history, or persistent result storage.
