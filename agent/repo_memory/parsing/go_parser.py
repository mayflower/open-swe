from __future__ import annotations

import tree_sitter as ts

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name
from .tree_sitter_loader import node_text, parse_source


def parse_go_entities(path: str, source: str) -> list[ParsedEntity]:
    tree = parse_source("go", source)
    source_bytes = source.encode("utf-8")
    entities: list[ParsedEntity] = []
    for child in tree.root_node.named_children:
        if child.type == "type_declaration":
            _emit_type_declaration(child, source_bytes, path, entities)
        elif child.type == "function_declaration":
            _emit_function(child, source_bytes, path, entities)
        elif child.type == "method_declaration":
            _emit_method(child, source_bytes, path, entities)
    return entities


def _emit_type_declaration(
    node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]
) -> None:
    for spec in node.named_children:
        if spec.type != "type_spec":
            continue
        name_node = spec.child_by_field_name("name")
        type_node = spec.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        name = node_text(name_node, source_bytes)
        kind = _map_type_kind(type_node.type)
        out.append(
            ParsedEntity(
                entity_id=make_entity_id(path, name),
                path=path,
                language="go",
                kind=kind,
                name=name,
                qualified_name=name,
                parent_qualified_name=None,
                signature=_first_line(node_text(spec, source_bytes), prefix="type "),
                docstring="",
                comment="",
                body=node_text(spec, source_bytes),
                start_line=spec.start_point[0] + 1,
                end_line=spec.end_point[0] + 1,
            )
        )


def _emit_function(
    node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    params = _params_text(node, source_bytes)
    signature = f"func {name}{params}"
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, name),
            path=path,
            language="go",
            kind=EntityKind.FUNCTION,
            name=name,
            qualified_name=name,
            parent_qualified_name=None,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_method(
    node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]
) -> None:
    name_node = node.child_by_field_name("name")
    receiver_node = node.child_by_field_name("receiver")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    receiver_type = _extract_receiver_type(receiver_node, source_bytes) if receiver_node else ""
    qualified_name = make_qualified_name(receiver_type or None, name)
    params = _params_text(node, source_bytes)
    receiver_text = node_text(receiver_node, source_bytes) if receiver_node else ""
    signature = f"func {receiver_text} {name}{params}".strip()
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="go",
            kind=EntityKind.METHOD,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=receiver_type or None,
            signature=signature,
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _extract_receiver_type(receiver: ts.Node, source_bytes: bytes) -> str:
    for param in receiver.named_children:
        if param.type != "parameter_declaration":
            continue
        type_node = param.child_by_field_name("type")
        if type_node is None:
            continue
        inner = type_node
        if inner.type == "pointer_type":
            named = [child for child in inner.named_children if child.type == "type_identifier"]
            if named:
                inner = named[0]
        return node_text(inner, source_bytes)
    return ""


def _params_text(node: ts.Node, source_bytes: bytes) -> str:
    params = node.child_by_field_name("parameters")
    if params is None:
        return "()"
    return node_text(params, source_bytes)


def _map_type_kind(type_name: str) -> EntityKind:
    if type_name == "struct_type":
        return EntityKind.STRUCT
    if type_name == "interface_type":
        return EntityKind.INTERFACE
    return EntityKind.TYPE


def _first_line(text: str, *, prefix: str = "") -> str:
    line = text.splitlines()[0] if text else ""
    rendered = f"{prefix}{line}" if prefix and not line.startswith(prefix) else line
    return rendered.strip().rstrip("{").strip()
