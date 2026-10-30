"""Build exact, bounded Java declaration chunks from symbols and AST spans."""

import hashlib
import json
import os
from bisect import bisect_left
from dataclasses import dataclass
from uuid import UUID

from tree_sitter import Node

from app.modules.code_intelligence.chunking.schemas import ExcludedSourceSpan, JavaChunk
from app.modules.code_intelligence.java_parser import JavaSyntaxTree
from app.modules.code_intelligence.schemas import JavaCallableSymbol, JavaTypeSymbol
from app.modules.code_intelligence.symbol_extractor import ExtractedJavaSymbols

DEFAULT_MAX_CHUNK_CHARS = 4000
MAX_CHUNK_CHARS = 1_000_000
BODY_NODES = {"block", "class_body", "switch_block"}
CONTROL_NODES = {
    "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
    "do_statement", "try_statement", "switch_expression", "synchronized_statement",
    "catch_clause", "finally_clause", "labeled_statement",
}


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    fallback: bool = False


class JavaAstChunker:
    def __init__(self, max_chunk_chars: int | None = None) -> None:
        configured = max_chunk_chars
        if configured is None:
            configured = int(os.environ.get("ARCHMIND_MAX_CHUNK_CHARS", DEFAULT_MAX_CHUNK_CHARS))
        if not 1 <= configured <= MAX_CHUNK_CHARS:
            raise ValueError(f"max_chunk_chars must be between 1 and {MAX_CHUNK_CHARS}")
        self.max_chunk_chars = configured

    def chunk(
        self,
        repository_id: UUID,
        file_path: str,
        syntax: JavaSyntaxTree,
        extracted: ExtractedJavaSymbols,
        max_chunk_chars: int | None = None,
    ) -> list[JavaChunk]:
        limit = max_chunk_chars if max_chunk_chars is not None else self.max_chunk_chars
        types = {
            symbol.qualified_name: symbol
            for symbol in extracted.symbols
            if isinstance(symbol, JavaTypeSymbol)
        }
        # Container class bodies would duplicate nested callable or class chunks.
        containers = {
            symbol.enclosing_type
            for symbol in extracted.symbols
            if symbol.enclosing_type is not None
        }
        source = syntax.source_bytes
        newline_offsets = [index for index, byte in enumerate(source) if byte == 10]
        chunks: list[JavaChunk] = []
        for symbol, node in zip(extracted.symbols, extracted.symbol_nodes, strict=True):
            if isinstance(symbol, JavaTypeSymbol):
                if symbol.kind != "class" or symbol.qualified_name in containers:
                    continue
                parent_type = types.get(symbol.enclosing_type)
                chunk_type = "class"
                annotations = _unique(
                    [*(parent_type.annotations if parent_type else []), *symbol.annotations]
                )
                parent_symbol = parent_type.name if parent_type else None
            elif isinstance(symbol, JavaCallableSymbol):
                parent_type = types[symbol.enclosing_type]
                chunk_type = symbol.kind
                annotations = _unique([*parent_type.annotations, *symbol.annotations])
                parent_symbol = parent_type.name
            else:
                continue

            header_parts = []
            if extracted.package_name:
                header_parts.append(f"Package: {extracted.package_name}")
            if isinstance(symbol, JavaTypeSymbol) and symbol.enclosing_type:
                header_parts.append(f"Enclosing type: {symbol.enclosing_type}")
            elif parent_type and parent_type.enclosing_type:
                header_parts.append(f"Enclosing type: {parent_type.enclosing_type}")
            if chunk_type == "class":
                header_parts.append(f"Class: {symbol.name}")
            else:
                header_parts.append(f"Class: {parent_symbol}")
                label = "Method" if chunk_type == "method" else "Constructor"
                header_parts.append(f"{label}: {symbol.name}")
            header_parts.extend(f"Annotation: {annotation}" for annotation in annotations)
            header = " | ".join(header_parts)

            content = source[node.start_byte : node.end_byte].decode("utf-8")
            parent_id = _identifier(
                repository_id, file_path, chunk_type, symbol.qualified_name,
                node.start_byte, node.end_byte, extracted.package_name,
                extracted.imports, annotations, content,
            )
            if len(content) <= limit:
                spans = [_Span(node.start_byte, node.end_byte)]
            else:
                spans = _pack(_split_node(node, source, limit), source, limit)
                if (
                    not spans or spans[0].start != node.start_byte
                    or spans[-1].end != node.end_byte
                    or any(left.end != right.start for left, right in zip(spans, spans[1:]))
                ):
                    raise ValueError("Chunk splitting failed to cover the declaration.")

            for index, span in enumerate(spans, start=1):
                child = len(spans) > 1
                part_content = source[span.start : span.end].decode("utf-8")
                chunk_id = (
                    _identifier(parent_id, index, span.start, span.end, part_content)
                    if child else parent_id
                )
                start_line = bisect_left(newline_offsets, span.start) + 1
                end_line = bisect_left(newline_offsets, span.end) + (
                    0 if source[span.end - 1] == 10 else 1
                )
                chunks.append(
                    JavaChunk(
                        chunk_id=chunk_id,
                        repository_id=repository_id,
                        file_path=file_path,
                        language="java",
                        chunk_type=f"{chunk_type}_part" if child else chunk_type,
                        parent_chunk_id=parent_id if child else None,
                        sequence=index if child else None,
                        split_strategy=("fallback" if span.fallback else "ast") if child else None,
                        start_byte=span.start,
                        end_byte=span.end,
                        symbol_name=symbol.name,
                        qualified_name=symbol.qualified_name,
                        parent_symbol=parent_symbol,
                        package_name=extracted.package_name,
                        imports=list(extracted.imports),
                        annotations=annotations,
                        start_line=start_line,
                        end_line=max(start_line, end_line),
                        content=part_content,
                        contextual_header=f"{header} | Part: {index}" if child else header,
                    )
                )
        return chunks


def _split_node(node: Node, source: bytes, limit: int) -> list[_Span]:
    if _char_length(source, node.start_byte, node.end_byte) <= limit:
        return [_Span(node.start_byte, node.end_byte)]

    children: list[Node]
    if node.type in {"method_declaration", "constructor_declaration", "class_declaration"}:
        body = node.child_by_field_name("body")
        children = [body] if body is not None else []
    elif node.type in BODY_NODES:
        children = list(node.named_children)
    elif node.type in CONTROL_NODES:
        children = [child for child in node.named_children if child.type in BODY_NODES]
    else:
        children = []

    if not children:
        return _fallback(source, node.start_byte, node.end_byte, limit)

    spans: list[_Span] = []
    cursor = node.start_byte
    for child in children:
        if cursor < child.start_byte:
            spans.extend(_fallback(source, cursor, child.start_byte, limit))
        spans.extend(_split_node(child, source, limit))
        cursor = child.end_byte
    if cursor < node.end_byte:
        spans.extend(_fallback(source, cursor, node.end_byte, limit))
    return spans


def _fallback(source: bytes, start: int, end: int, limit: int) -> list[_Span]:
    # UTF-8 code points are never cut in half. A short gap is not itself a
    # fallback split; it can be packed with neighboring AST units.
    text = source[start:end].decode("utf-8")
    spans: list[_Span] = []
    cursor = start
    for index in range(0, len(text), limit):
        part = text[index : index + limit]
        next_cursor = cursor + len(part.encode("utf-8"))
        spans.append(_Span(cursor, next_cursor, len(text) > limit))
        cursor = next_cursor
    return spans


def _pack(spans: list[_Span], source: bytes, limit: int) -> list[_Span]:
    packed: list[_Span] = []
    for span in spans:
        if (
            packed and packed[-1].end == span.start
            and _char_length(source, packed[-1].start, span.end) <= limit
        ):
            previous = packed[-1]
            packed[-1] = _Span(previous.start, span.end, previous.fallback or span.fallback)
        else:
            packed.append(span)
    return packed


def _char_length(source: bytes, start: int, end: int) -> int:
    return len(source[start:end].decode("utf-8"))


def _identifier(*parts: object) -> str:
    identity = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def excluded_source_spans(source: bytes, chunks: list[JavaChunk]) -> list[ExcludedSourceSpan]:
    """Account for every byte outside emitted declarations without copying it."""
    newline_offsets = [index for index, byte in enumerate(source) if byte == 10]
    excluded: list[ExcludedSourceSpan] = []
    cursor = 0
    for chunk in sorted(chunks, key=lambda item: item.start_byte):
        if chunk.start_byte < cursor:
            raise ValueError("Chunk spans overlap.")
        if cursor < chunk.start_byte:
            excluded.append(_excluded_span(source, newline_offsets, cursor, chunk.start_byte))
        cursor = chunk.end_byte
    if cursor < len(source):
        excluded.append(_excluded_span(source, newline_offsets, cursor, len(source)))
    return excluded


def _excluded_span(
    source: bytes, newline_offsets: list[int], start: int, end: int
) -> ExcludedSourceSpan:
    start_line = bisect_left(newline_offsets, start) + 1
    end_line = bisect_left(newline_offsets, end) + (0 if source[end - 1] == 10 else 1)
    return ExcludedSourceSpan(
        start_byte=start,
        end_byte=end,
        start_line=start_line,
        end_line=max(start_line, end_line),
        reason="outside_chunked_declarations",
    )
