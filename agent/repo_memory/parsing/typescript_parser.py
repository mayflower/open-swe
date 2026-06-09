from __future__ import annotations

import tree_sitter as ts

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name
from .tree_sitter_loader import node_text, parse_source


def parse_typescript_entities(path: str, source: str) -> list[ParsedEntity]:
    dialect = "tsx" if path.endswith(".tsx") else "typescript"
    tree = parse_source(dialect, source)
    source_bytes = source.encode("utf-8")
    entities: list[ParsedEntity] = []
    _walk(tree.root_node, source_bytes, path, parent=None, out=entities)
    return entities


def _walk(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    for child in node.children:
        if child.type == "export_statement":
            _walk(child, source_bytes, path, parent=parent, out=out)
            continue
        if child.type == "class_declaration":
            _emit_class(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "interface_declaration":
            _emit_interface(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "function_declaration":
            _emit_function(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "type_alias_declaration":
            _emit_type_alias(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "lexical_declaration":
            _emit_arrow_function_declarations(child, source_bytes, path, parent=parent, out=out)


def _emit_class(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    qualified_name = make_qualified_name(parent, name)
    signature = _first_line(node_text(node, source_bytes))
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="typescript",
            kind=EntityKind.CLASS,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )
    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.named_children:
        if member.type == "method_definition":
            _emit_method(member, source_bytes, path, parent=qualified_name, out=out)


def _emit_interface(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    qualified_name = make_qualified_name(parent, name)
    signature = _first_line(node_text(node, source_bytes))
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="typescript",
            kind=EntityKind.INTERFACE,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_type_alias(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    qualified_name = make_qualified_name(parent, name)
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="typescript",
            kind=EntityKind.TYPE,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=_first_line(node_text(node, source_bytes)),
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_function(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    qualified_name = make_qualified_name(parent, name)
    params = _params_text(node, source_bytes)
    signature = f"function {name}{params}"
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="typescript",
            kind=EntityKind.FUNCTION,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_method(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    qualified_name = make_qualified_name(parent, name)
    params = _params_text(node, source_bytes)
    signature = f"{name}{params}"
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="typescript",
            kind=EntityKind.METHOD,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_arrow_function_declarations(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    for declarator in node.named_children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is None or value.type not in {"arrow_function", "function_expression"}:
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        name = node_text(name_node, source_bytes)
        qualified_name = make_qualified_name(parent, name)
        params = _params_text(value, source_bytes)
        signature = f"const {name} = {params} => ..."
        out.append(
            ParsedEntity(
                entity_id=make_entity_id(path, qualified_name),
                path=path,
                language="typescript",
                kind=EntityKind.FUNCTION,
                name=name,
                qualified_name=qualified_name,
                parent_qualified_name=parent,
                signature=signature,
                docstring="",
                comment="",
                body=node_text(declarator, source_bytes),
                start_line=declarator.start_point[0] + 1,
                end_line=declarator.end_point[0] + 1,
            )
        )


def _params_text(node: ts.Node, source_bytes: bytes) -> str:
    params = node.child_by_field_name("parameters")
    if params is None:
        return "()"
    return node_text(params, source_bytes)


def _first_line(text: str) -> str:
    line = text.splitlines()[0] if text else ""
    return line.strip().rstrip("{").strip()
