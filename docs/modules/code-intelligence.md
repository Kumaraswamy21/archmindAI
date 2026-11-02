# Code intelligence: Java AST parsing, symbols, and chunks

Phase 3.1 adds a separate `backend/app/modules/code_intelligence/` module. Phase 3.2 adds Java symbol extraction from the same Tree-sitter AST. Phase 3.3 adds deterministic AST-aware chunk previews. Its router exposes the APIs, schemas define JSON, the service obtains source text through Phase 2's secure reader, the Java parser adapter builds the AST, and a separate extractor derives symbol metadata. The chunker uses those symbols and their AST spans. No repository files are modified or executed. These phases do not add embeddings, RAG, dependency graphs, a database, or LLM calls; the [runtime prompt policy](../prompt-management.md) remains in force for future LLM features.

## API contract

`POST /api/v1/repositories/{repository_id}/parse` accepts:

```json
{"file_path":"src/main/java/com/example/OrderService.java"}
```

The path must be repository-relative. The service reads it with the same path, symlink, UTF-8, and file-size protections as `GET /api/v1/repositories/{repository_id}/files/content`. Only `.java` files are parsed. HTTP 200 returns:

```json
{
  "file_path": "src/main/java/com/example/OrderService.java",
  "language": "java",
  "has_syntax_errors": false,
  "classes": [
    {
      "name": "OrderService",
      "start_line": 5,
      "end_line": 18,
      "methods": [
        {"name": "createOrder", "start_line": 9, "end_line": 16}
      ]
    }
  ]
}
```

The first version includes top-level Java class declarations and methods directly inside each class. Nested classes, constructors, interfaces, enums, and records are outside this response. Multiple top-level classes and direct methods appear in source order. Line numbers are one-based and inclusive of the last occupied line. An empty file returns `classes: []` and `has_syntax_errors: false`. Malformed source returns recoverable declarations with `has_syntax_errors: true`; parser nodes are never returned over the API.

Application errors use the shared `{"error":{"code":"...","message":"..."}}` envelope:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `UNSUPPORTED_SOURCE_FILE` or `UNSUPPORTED_PARSE_LANGUAGE` | The file type is unsupported or is not Java. |
| 403 | `INVALID_SOURCE_PATH` | The path is invalid, escapes the root, or crosses a symlink. |
| 404 | `REPOSITORY_NOT_FOUND` or `SOURCE_FILE_NOT_FOUND` | The repository or file is missing. |
| 413 | `SOURCE_FILE_TOO_LARGE` | The file exceeds the configured size limit. |
| 422 | `FILE_ENCODING_INVALID` | The file is not valid UTF-8. |
| 503 | `REPOSITORY_UNAVAILABLE` | The registered repository cannot be read. |

Missing request fields and invalid UUIDs receive the shared HTTP 422 `VALIDATION_ERROR` response. OpenAPI documents this shape.

## Java symbols API

`GET /api/v1/repositories/{repository_id}/symbols?file_path=src/main/java/com/example/OrderService.java` reads one registered Java file through the same secure reader and returns package, imports, and structural symbols. The response uses typed schemas rather than Tree-sitter node objects:

```json
{
  "file_path": "src/main/java/com/example/OrderService.java",
  "package": "com.example",
  "imports": ["org.springframework.stereotype.Service"],
  "symbols": [
    {
      "name": "OrderService",
      "kind": "class",
      "qualified_name": "com.example.OrderService",
      "enclosing_type": null,
      "annotations": ["@Service"],
      "start_line": 5,
      "end_line": 18
    },
    {
      "name": "createOrder",
      "kind": "method",
      "qualified_name": "com.example.OrderService.createOrder",
      "enclosing_type": "com.example.OrderService",
      "signature": "public Order createOrder(OrderRequest request)",
      "parameters": [{"name": "request", "type": "OrderRequest"}],
      "annotations": [],
      "start_line": 9,
      "end_line": 16
    }
  ]
}
```

The extractor includes classes, interfaces, their direct methods and constructors, and nested member classes/interfaces. A top-level type has `enclosing_type: null`; a nested or callable symbol gives the enclosing type's dotted qualified name. Constructor names repeat their class name, so a constructor in `com.example.OrderService` has qualified name `com.example.OrderService.OrderService`. Overloaded methods share a qualified name and remain distinguishable by their signatures. Symbols and imports appear in source order. Files without a package return `package: null`; empty files return empty `imports` and `symbols` arrays.

Method and constructor signatures include declaration text through the parameter list and any `throws` clause, with whitespace normalized and declaration-level annotations and bodies excluded. Parameter types retain source spelling, including generics, arrays, and varargs. Annotation strings retain their source arguments, such as `@GetMapping("/orders")`; static imports are prefixed with `static`. Common Spring annotations including `@RestController`, `@Service`, `@Repository`, `@GetMapping`, `@PostMapping`, and `@Transactional` are captured as source metadata. Their presence does not prove runtime behavior. Imports are recorded, not resolved; no external dependency lookup or call graph is built.

Locations use one-based, inclusive lines and include declaration-level annotation lines when present. Malformed Java returns symbols Tree-sitter can recover; the Phase 3.1 parse endpoint remains available when syntax-error presence is needed. The symbols endpoint inherits the reader and parse endpoint's HTTP 400, 403, 404, 413, 422, and 503 errors. Invalid query parameters use the shared 422 `VALIDATION_ERROR` envelope.

## Java chunk preview API

`POST /api/v1/repositories/{repository_id}/chunks/preview` accepts a repository-relative Java file path and an optional character limit:

```json
{"file_path":"src/main/java/com/example/OrderService.java","max_chunk_chars":1500}
```

HTTP 200 returns `file_path`, `total_chunks`, `chunks`, and `excluded_spans`. Every chunk includes `chunk_id`, `repository_id`, `file_path`, `language`, `chunk_type`, `parent_chunk_id`, `sequence`, `split_strategy`, `start_byte`, `end_byte`, `symbol_name`, `qualified_name`, `parent_symbol`, `package_name`, `imports`, `annotations`, `start_line`, `end_line`, `content`, and `contextual_header`. For example:

```json
{
  "file_path": "src/main/java/com/example/OrderService.java",
  "total_chunks": 1,
  "chunks": [
    {
      "chunk_id": "a SHA-256 hexadecimal identifier",
      "parent_chunk_id": null,
      "sequence": null,
      "split_strategy": null,
      "start_byte": 200,
      "end_byte": 263,
      "repository_id": "the registered repository UUID",
      "file_path": "src/main/java/com/example/OrderService.java",
      "language": "java",
      "chunk_type": "method",
      "symbol_name": "createOrder",
      "qualified_name": "com.example.OrderService.createOrder",
      "parent_symbol": "OrderService",
      "package_name": "com.example",
      "imports": ["org.springframework.stereotype.Service"],
      "annotations": ["@Service"],
      "start_line": 9,
      "end_line": 16,
      "content": "public Order createOrder(OrderRequest request) { /* source */ }",
      "contextual_header": "Package: com.example | Class: OrderService | Method: createOrder | Annotation: @Service"
    }
  ],
  "excluded_spans": [
    {"start_byte": 0, "end_byte": 200, "start_line": 1, "end_line": 8, "reason": "outside_chunked_declarations"}
  ]
}
```

The example illustrates the response shape; actual `content` is the exact UTF-8 substring occupied by the Tree-sitter declaration node, including its original whitespace and annotations. The header is generated only from extracted package, enclosing type, declaration, and annotation facts. It makes no claims about runtime behavior. Imports and class and callable annotations live in metadata, so complete class bodies are not copied into each method chunk.

Each direct method and constructor becomes one complete chunk when it fits the limit. A leaf class with no methods, constructors, or nested declarations becomes one class chunk when it fits. Container classes are not emitted as class chunks because that would duplicate their child source. Interfaces contribute their method chunks but do not produce empty interface chunks. Empty Java files produce zero chunks. Recoverable declarations in malformed files may produce chunks; use the parse endpoint to inspect syntax-error presence.

Chunk IDs are SHA-256 hashes of the repository UUID, relative file path, chunk kind, qualified name, AST byte boundaries, package, imports, annotations, and exact source content. Repeating a preview against identical registered content yields the same IDs, including for overloaded methods on the same line. A new repository registration gets a new UUID and thus different IDs. Line numbers are one-based and inclusive.

`ARCHMIND_MAX_CHUNK_CHARS` sets the default preview limit to 4,000 Python Unicode characters; it is read at application startup. The optional `max_chunk_chars` request field overrides it for one preview. Both accept 1 through 1,000,000 characters; an invalid request value returns the shared HTTP 422 validation response. Character counts are deterministic for a given decoded UTF-8 source but are only an approximation of a future tokenizer's budget.

An oversized declaration is partitioned at Tree-sitter method, constructor, class body, and statement boundaries where possible. Each part has type `method_part`, `constructor_part`, or `class_part`, plus a shared `parent_chunk_id`, one-based `sequence`, byte offsets, and a header with `Part: N`. The parent ID is the ID the intact declaration would have; it is a reference, not a duplicate parent chunk in the response. A single remaining AST node or intervening source span that exceeds the limit is cut at Unicode character boundaries and marked `split_strategy: "fallback"`; parts formed from AST boundaries are marked `"ast"`. All parts preserve exact source text, and concatenating them in sequence reconstructs the full declaration. Byte offsets refer to the UTF-8 file and allow exact verification even for multibyte characters. No source is silently truncated.

`excluded_spans` lists every byte range outside emitted chunks, including package/import text, class wrappers, comments, fields in container classes, and unsupported declarations. Each item has byte offsets, one-based line range, and reason `outside_chunked_declarations`. The chunks and excluded spans together cover the entire file without overlap. Excluded spans do not echo source text. This phase does not promise semantic completeness of malformed Java or chunk top-level fields/initializers separately.

The preview inherits the secure reader's file-size limit (`ARCHMIND_DISCOVERY_MAX_FILE_SIZE_BYTES`), path and symlink protections, and UTF-8 validation. An oversized file returns HTTP 413 before parsing. Error codes and HTTP statuses are the same as the parse and symbols endpoints: 400, 403, 404, 413, 422, and 503. Request validation uses the shared 422 `VALIDATION_ERROR` response. No chunks are persisted.

## Dependencies and verification

`tree-sitter` and `tree-sitter-java` are pinned in `backend/pyproject.toml` and installed with the normal backend setup command. The parser creates a Tree-sitter syntax tree from UTF-8 source bytes, checks for syntax errors, and extracts names and positions without exposing parser-specific objects. Tests use temporary registered repositories and cover valid, empty, and malformed Java files, multiple declarations, source-reader errors, and OpenAPI.
