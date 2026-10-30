"""Tree-sitter adapter for top-level Java classes and direct methods."""

from dataclasses import dataclass

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Tree


JAVA_LANGUAGE = Language(tree_sitter_java.language())


@dataclass(frozen=True)
class ParsedMethod:
    name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ParsedClass:
    name: str
    start_line: int
    end_line: int
    methods: tuple[ParsedMethod, ...]


@dataclass(frozen=True)
class ParsedJava:
    has_syntax_errors: bool
    classes: tuple[ParsedClass, ...]


@dataclass(frozen=True)
class JavaSyntaxTree:
    tree: Tree
    source_bytes: bytes


class JavaParserAdapter:
    def parse_tree(self, source_text: str) -> JavaSyntaxTree:
        source_bytes = source_text.encode("utf-8")
        tree = Parser(JAVA_LANGUAGE).parse(source_bytes)
        if tree is None:
            raise RuntimeError("Java parser did not return a syntax tree.")
        return JavaSyntaxTree(tree=tree, source_bytes=source_bytes)

    def parse(self, source_text: str) -> ParsedJava:
        syntax = self.parse_tree(source_text)
        tree = syntax.tree
        source_bytes = syntax.source_bytes

        classes: list[ParsedClass] = []
        for node in tree.root_node.named_children:
            if node.type != "class_declaration":
                continue
            name = _name(node, source_bytes)
            if name is None:
                continue

            methods: list[ParsedMethod] = []
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    if child.type != "method_declaration":
                        continue
                    method_name = _name(child, source_bytes)
                    if method_name is None:
                        continue
                    methods.append(
                        ParsedMethod(
                            name=method_name,
                            start_line=_start_line(child),
                            end_line=_end_line(child),
                        )
                    )

            classes.append(
                ParsedClass(
                    name=name,
                    start_line=_start_line(node),
                    end_line=_end_line(node),
                    methods=tuple(methods),
                )
            )

        return ParsedJava(
            has_syntax_errors=tree.root_node.has_error,
            classes=tuple(classes),
        )


def _name(node: Node, source_bytes: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is None or name.is_missing:
        return None
    return source_bytes[name.start_byte : name.end_byte].decode("utf-8")


def _start_line(node: Node) -> int:
    return node.start_point.row + 1


def _end_line(node: Node) -> int:
    end = node.end_point
    return max(_start_line(node), end.row + (1 if end.column else 0))
