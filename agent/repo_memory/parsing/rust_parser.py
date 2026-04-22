from __future__ import annotations

import tree_sitter as ts

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name
from .tree_sitter_loader import node_text, parse_source


def parse_rust_entities(path: str, source: str) -> list[ParsedEntity]:
    tree = parse_source("rust", source)
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
        if child.type == "trait_item":
            _emit_trait(child, source_bytes, path, out)
        elif child.type == "struct_item":
            _emit_simple_type(child, source_bytes, path, EntityKind.STRUCT, out)
        elif child.type == "enum_item":
            _emit_simple_type(child, source_bytes, path, EntityKind.ENUM, out)
        elif child.type == "impl_item":
            _emit_impl(child, source_bytes, path, out)
        elif child.type == "function_item":
            _emit_function(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "type_item":
            _emit_type_alias(child, source_bytes, path, out)
        elif child.type == "mod_item":
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, source_bytes, path, parent=parent, out=out)


def _emit_trait(
    node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, name),
            path=path,
            language="rust",
            kind=EntityKind.TRAIT,
            name=name,
            qualified_name=name,
            parent_qualified_name=None,
            signature=_first_line(node_text(node, source_bytes)),
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
        if member.type == "function_signature_item":
            _emit_function_signature(member, source_bytes, path, parent=name, out=out)
        elif member.type == "function_item":
            _emit_function(member, source_bytes, path, parent=name, out=out)


def _emit_simple_type(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    kind: EntityKind,
    out: list[ParsedEntity],
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, name),
            path=path,
            language="rust",
            kind=kind,
            name=name,
            qualified_name=name,
            parent_qualified_name=None,
            signature=_first_line(node_text(node, source_bytes)),
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )


def _emit_impl(node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]) -> None:
    type_node = node.child_by_field_name("type")
    trait_node = node.child_by_field_name("trait")
    parent_name = ""
    if type_node is not None:
        parent_name = node_text(type_node, source_bytes)
    if trait_node is not None:
        trait_name = node_text(trait_node, source_bytes)
        parent_name = f"{trait_name} for {parent_name}" if parent_name else trait_name
    body = node.child_by_field_name("body")
    if body is None or not parent_name:
        return
    impl_parent = parent_name.split(" for ")[-1].strip() or parent_name
    for member in body.named_children:
        if member.type == "function_item":
            _emit_function(member, source_bytes, path, parent=impl_parent, out=out)
        elif member.type == "function_signature_item":
            _emit_function_signature(member, source_bytes, path, parent=impl_parent, out=out)


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
    signature = f"fn {name}{params}"
    kind = EntityKind.METHOD if parent else EntityKind.FUNCTION
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="rust",
            kind=kind,
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


def _emit_function_signature(
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
    signature = f"fn {name}{params}"
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="rust",
            kind=EntityKind.METHOD if parent else EntityKind.FUNCTION,
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
    node: ts.Node, source_bytes: bytes, path: str, out: list[ParsedEntity]
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = node_text(name_node, source_bytes)
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, name),
            path=path,
            language="rust",
            kind=EntityKind.TYPE,
            name=name,
            qualified_name=name,
            parent_qualified_name=None,
            signature=_first_line(node_text(node, source_bytes)),
            docstring="",
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
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
