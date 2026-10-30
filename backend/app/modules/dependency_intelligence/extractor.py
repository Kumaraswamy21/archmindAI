"""Extract a conservative set of Java dependency edges from existing ASTs."""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from tree_sitter import Node

from app.modules.code_intelligence.java_parser import JavaSyntaxTree, _end_line, _start_line
from app.modules.code_intelligence.schemas import JavaCallableSymbol, JavaTypeSymbol
from app.modules.code_intelligence.symbol_extractor import ExtractedJavaSymbols
from app.modules.dependency_intelligence.schemas import DependencyRelationship, RelationshipType


SPRING_COMPONENTS = ("@Service", "@Repository", "@RestController", "@Controller", "@Component", "@Configuration")
INJECTION_ANNOTATIONS = ("@Autowired", "@Inject")
NON_INJECTABLE = {"byte", "short", "int", "long", "float", "double", "boolean", "char", "String"}


@dataclass(frozen=True)
class JavaSourceUnit:
    file_path: str
    syntax: JavaSyntaxTree
    extracted: ExtractedJavaSymbols


class JavaDependencyExtractor:
    def extract(self, repository_id: UUID, units: list[JavaSourceUnit]) -> list[DependencyRelationship]:
        # Only declarations in this repository can be resolved.
        types = {
            symbol.qualified_name: symbol
            for unit in units for symbol in unit.extracted.symbols
            if isinstance(symbol, JavaTypeSymbol)
        }
        methods: dict[tuple[str, str], list[JavaCallableSymbol]] = {}
        for unit in units:
            for symbol in unit.extracted.symbols:
                if isinstance(symbol, JavaCallableSymbol) and symbol.kind == "method":
                    methods.setdefault((symbol.enclosing_type, symbol.name), []).append(symbol)
        edges: list[DependencyRelationship] = []
        for unit in units:
            edges.extend(self._unit_edges(repository_id, unit, types, methods))
        return sorted(edges, key=lambda edge: (
            edge.file_path, edge.start_line, edge.end_line,
            edge.relationship_type, edge.source_symbol, edge.target_symbol,
            edge.relationship_id,
        ))

    def _unit_edges(
        self, repository_id: UUID, unit: JavaSourceUnit,
        types: dict[str, JavaTypeSymbol],
        methods: dict[tuple[str, str], list[JavaCallableSymbol]],
    ) -> list[DependencyRelationship]:
        source = unit.syntax.source_bytes
        package = unit.extracted.package_name
        imports = {
            name.rsplit(".", 1)[-1]: name
            for name in unit.extracted.imports
            if not name.startswith("static ") and not name.endswith(".*")
        }

        def resolve_type(name: str) -> tuple[str, str]:
            candidates = [name] if "." in name else [
                f"{package}.{name}" if package else name,
                imports.get(name, ""),
            ]
            matches = {candidate for candidate in candidates if candidate in types}
            if len(matches) == 1:
                return matches.pop(), "RESOLVED"
            return name, "UNRESOLVED"

        def edge(source_symbol: str, target: str, kind: RelationshipType, node: Node, status: str) -> DependencyRelationship:
            identity = "\0".join((
                str(repository_id), unit.file_path, source_symbol, target, kind,
                str(node.start_byte), str(node.end_byte),
            ))
            return DependencyRelationship(
                relationship_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                repository_id=repository_id, source_symbol=source_symbol,
                target_symbol=target, relationship_type=kind,
                file_path=unit.file_path, start_line=_start_line(node),
                end_line=_end_line(node), resolution_status=status,
            )

        edges: list[DependencyRelationship] = []
        for node in unit.syntax.tree.root_node.named_children:
            if node.type == "import_declaration":
                name = _text(node, source).removeprefix("import").removesuffix(";").strip()
                target = name.removeprefix("static ").strip()
                status = "RESOLVED" if name == target and target in types else "UNRESOLVED"
                edges.append(edge(f"file:{unit.file_path}", target, "IMPORTS", node, status))

        for symbol, node in zip(unit.extracted.symbols, unit.extracted.symbol_nodes):
            if isinstance(symbol, JavaTypeSymbol):
                for child in node.named_children:
                    kind = (
                        "EXTENDS" if child.type in ("superclass", "extends_interfaces")
                        else "IMPLEMENTS" if child.type == "super_interfaces"
                        else None
                    )
                    if kind:
                        for type_node in _type_references(child):
                            target, status = resolve_type(_text(type_node, source))
                            edges.append(edge(symbol.qualified_name, target, kind, type_node, status))
                body = node.child_by_field_name("body")
                if body is None:
                    continue
                fields: dict[str, str] = {}
                constructors = [child for child in body.named_children if child.type == "constructor_declaration"]
                is_component = any(
                    annotation.split("(", 1)[0] in SPRING_COMPONENTS
                    for annotation in symbol.annotations
                )
                for child in body.named_children:
                    if child.type != "field_declaration":
                        continue
                    type_node = child.child_by_field_name("type")
                    if type_node is None:
                        continue
                    type_name = _text(type_node, source)
                    for declarator in child.named_children:
                        if declarator.type != "variable_declarator":
                            continue
                        name_node = declarator.child_by_field_name("name")
                        if name_node is None:
                            continue
                        fields[_text(name_node, source)] = type_name
                        if _has_injection_annotation(child, source) and type_name not in NON_INJECTABLE:
                            target, status = resolve_type(type_name)
                            edges.append(edge(symbol.qualified_name, target, "INJECTS", declarator, status))
                for constructor in constructors:
                    if not (_has_injection_annotation(constructor, source) or (is_component and len(constructors) == 1)):
                        continue
                    params = next((part for part in constructor.named_children if part.type == "formal_parameters"), None)
                    if params is None:
                        continue
                    for param in params.named_children:
                        if param.type != "formal_parameter":
                            continue
                        type_node = param.child_by_field_name("type")
                        if type_node is None:
                            continue
                        type_name = _text(type_node, source)
                        if type_name not in NON_INJECTABLE:
                            target, status = resolve_type(type_name)
                            edges.append(edge(symbol.qualified_name, target, "INJECTS", param, status))
                # Each callable uses the field types of its direct enclosing type.
                for callable_symbol, callable_node in zip(unit.extracted.symbols, unit.extracted.symbol_nodes):
                    if not isinstance(callable_symbol, JavaCallableSymbol) or callable_symbol.enclosing_type != symbol.qualified_name:
                        continue
                    parameter_types = {param.name: param.type for param in callable_symbol.parameters}
                    local_names = _local_names(callable_node.child_by_field_name("body"), source)
                    for call in _calls(callable_node.child_by_field_name("body")):
                        name_node = call.child_by_field_name("name")
                        if name_node is None:
                            continue
                        method_name = _text(name_node, source)
                        receiver = call.child_by_field_name("object")
                        owner: str | None = None
                        if receiver is None or receiver.type == "this":
                            owner = symbol.qualified_name
                        elif receiver.type == "identifier":
                            label = _text(receiver, source)
                            if label not in local_names:
                                type_name = parameter_types.get(label) or fields.get(label)
                                if type_name is not None:
                                    candidate, status = resolve_type(type_name)
                                    if status == "RESOLVED":
                                        owner = candidate
                        elif receiver.type == "field_access":
                            object_node = receiver.child_by_field_name("object")
                            field_node = receiver.child_by_field_name("field")
                            if object_node is not None and object_node.type == "this" and field_node is not None:
                                candidate, status = resolve_type(fields.get(_text(field_node, source), ""))
                                if status == "RESOLVED":
                                    owner = candidate
                        matching = methods.get((owner, method_name), []) if owner else []
                        if len(matching) == 1:
                            target, status = matching[0].qualified_name, "RESOLVED"
                        else:
                            target = f"{_text(receiver, source)}.{method_name}" if receiver else method_name
                            status = "UNRESOLVED"
                        edges.append(edge(callable_symbol.qualified_name, target, "CALLS", call, status))
        return edges


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _type_references(node: Node) -> list[Node]:
    if node.type in ("type_identifier", "scoped_type_identifier"):
        return [node]
    if node.type == "generic_type":
        return _type_references(node.named_children[0]) if node.named_children else []
    result: list[Node] = []
    for child in node.named_children:
        result.extend(_type_references(child))
    return result


def _has_injection_annotation(node: Node, source: bytes) -> bool:
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    return bool(modifiers and any(
        child.type in ("annotation", "marker_annotation")
        and _text(child, source).split("(", 1)[0] in INJECTION_ANNOTATIONS
        for child in modifiers.named_children
    ))


def _local_names(node: Node | None, source: bytes) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    if node.type == "local_variable_declaration":
        for child in node.named_children:
            if child.type == "variable_declarator":
                name = child.child_by_field_name("name")
                if name is not None:
                    names.add(_text(name, source))
    for child in node.named_children:
        names.update(_local_names(child, source))
    return names


def _calls(node: Node | None):
    if node is None:
        return
    if node.type in ("class_declaration", "interface_declaration", "method_declaration", "constructor_declaration"):
        return
    if node.type == "method_invocation":
        yield node
    for child in node.named_children:
        yield from _calls(child)
