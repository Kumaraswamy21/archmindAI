"""Extract Java symbols and structural context from a Tree-sitter syntax tree."""

from dataclasses import dataclass

from tree_sitter import Node

from app.modules.code_intelligence.java_parser import (
    JavaSyntaxTree,
    _end_line,
    _start_line,
)
from app.modules.code_intelligence.schemas import (
    JavaCallableSymbol,
    JavaSymbolParameter,
    JavaTypeSymbol,
)

TYPE_NODES = {"class_declaration": "class", "interface_declaration": "interface"}
CALLABLE_NODES = {
    "method_declaration": "method",
    "constructor_declaration": "constructor",
}
ANNOTATION_NODES = {"annotation", "marker_annotation"}


@dataclass(frozen=True)
class ExtractedJavaSymbols:
    package_name: str | None
    imports: tuple[str, ...]
    symbols: tuple[JavaTypeSymbol | JavaCallableSymbol, ...]
    symbol_nodes: tuple[Node, ...]


class JavaSymbolExtractor:
    def extract(self, syntax: JavaSyntaxTree) -> ExtractedJavaSymbols:
        source = syntax.source_bytes
        root = syntax.tree.root_node
        package_name: str | None = None
        imports: list[str] = []
        symbols: list[JavaTypeSymbol | JavaCallableSymbol] = []
        symbol_nodes: list[Node] = []

        for node in root.named_children:
            if node.type == "package_declaration":
                package_name = _package_name(node, source)
            elif node.type == "import_declaration":
                imports.append(_import_name(node, source))
            elif node.type in TYPE_NODES:
                _visit_type(node, source, package_name, None, symbols, symbol_nodes)

        return ExtractedJavaSymbols(
            package_name=package_name,
            imports=tuple(imports),
            symbols=tuple(symbols),
            symbol_nodes=tuple(symbol_nodes),
        )


def _visit_type(
    node: Node,
    source: bytes,
    package_name: str | None,
    enclosing_type: str | None,
    symbols: list[JavaTypeSymbol | JavaCallableSymbol],
    symbol_nodes: list[Node],
) -> None:
    name = _name(node, source)
    if name is None:
        return
    qualified_name = f"{enclosing_type}.{name}" if enclosing_type else (
        f"{package_name}.{name}" if package_name else name
    )
    symbols.append(
        JavaTypeSymbol(
            name=name,
            kind=TYPE_NODES[node.type],
            qualified_name=qualified_name,
            enclosing_type=enclosing_type,
            annotations=_annotations(node, source),
            start_line=_start_line(node),
            end_line=_end_line(node),
        )
    )
    symbol_nodes.append(node)

    body = node.child_by_field_name("body")
    if body is None:
        return
    for child in body.named_children:
        if child.type in TYPE_NODES:
            _visit_type(child, source, package_name, qualified_name, symbols, symbol_nodes)
        elif child.type in CALLABLE_NODES:
            callable_name = _name(child, source)
            if callable_name is None:
                continue
            symbols.append(
                JavaCallableSymbol(
                    name=callable_name,
                    kind=CALLABLE_NODES[child.type],
                    qualified_name=f"{qualified_name}.{callable_name}",
                    enclosing_type=qualified_name,
                    signature=_signature(child, source),
                    parameters=_parameters(child, source),
                    annotations=_annotations(child, source),
                    start_line=_start_line(child),
                    end_line=_end_line(child),
                )
            )
            symbol_nodes.append(child)


def _package_name(node: Node, source: bytes) -> str | None:
    for child in node.named_children:
        if child.type in ("identifier", "scoped_identifier"):
            return _text(child, source)
    return None


def _import_name(node: Node, source: bytes) -> str:
    declaration = _text(node, source).strip()
    return declaration.removeprefix("import").removesuffix(";").strip()


def _name(node: Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is None or name.is_missing:
        return None
    return _text(name, source)


def _annotations(node: Node, source: bytes) -> list[str]:
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    if modifiers is None:
        return []
    return [
        _text(child, source).strip()
        for child in modifiers.named_children
        if child.type in ANNOTATION_NODES
    ]


def _signature(node: Node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    annotations = (
        [child for child in modifiers.named_children if child.type in ANNOTATION_NODES]
        if modifiers is not None else []
    )
    segments: list[bytes] = []
    cursor = node.start_byte
    for annotation in annotations:
        segments.append(source[cursor : annotation.start_byte])
        segments.append(b" ")
        cursor = annotation.end_byte
    segments.append(source[cursor:end])
    header = b"".join(segments).decode("utf-8").strip().removesuffix(";")
    normalized = " ".join(header.split())
    return normalized.replace("( ", "(").replace(" )", ")")


def _parameters(node: Node, source: bytes) -> list[JavaSymbolParameter]:
    parameter_list = node.child_by_field_name("parameters")
    if parameter_list is None:
        return []
    parameters: list[JavaSymbolParameter] = []
    for parameter in parameter_list.named_children:
        if parameter.type == "formal_parameter":
            type_node = parameter.child_by_field_name("type")
            name_node = parameter.child_by_field_name("name")
            if type_node is None or name_node is None or name_node.is_missing:
                continue
            parameters.append(
                JavaSymbolParameter(
                    name=_text(name_node, source), type=_text(type_node, source)
                )
            )
        elif parameter.type == "spread_parameter":
            type_node = parameter.named_children[0] if parameter.named_children else None
            declarator = next(
                (child for child in parameter.named_children if child.type == "variable_declarator"),
                None,
            )
            name_node = declarator.child_by_field_name("name") if declarator else None
            if type_node is None or name_node is None or name_node.is_missing:
                continue
            parameters.append(
                JavaSymbolParameter(
                    name=_text(name_node, source), type=f"{_text(type_node, source)}..."
                )
            )
    return parameters


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")
