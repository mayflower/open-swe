from __future__ import annotations

from dataclasses import dataclass

import tree_sitter as ts

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name
from .retrieval_text import build_retrieval_text
from .tree_sitter_loader import node_text, parse_source


@dataclass(slots=True)
class PythonParseResult:
    entities: list[ParsedEntity]


def parse_python_entities(path: str, source: str) -> PythonParseResult:
    tree = parse_source("python", source)
    source_bytes = source.encode("utf-8")
    entities: list[ParsedEntity] = []

    module_name = path.rsplit("/", 1)[-1] or path
    entities.append(
        ParsedEntity(
            entity_id=make_entity_id(path, module_name),
            path=path,
            language="python",
            kind=EntityKind.MODULE,
            name=module_name,
            qualified_name=module_name,
            parent_qualified_name=None,
            signature="module",
            docstring=_module_docstring(tree.root_node, source_bytes),
            comment="",
            body=source,
            start_line=1,
            end_line=max(tree.root_node.end_point[0] + 1, 1),
        )
    )
    _walk(tree.root_node, source_bytes, path, parent=None, out=entities)
    return PythonParseResult(entities=entities)


def parse_python_revisions(repo: str, path: str, source: str, observed_seq: int) -> list:
    return [
        entity.to_revision(
            repo=repo,
            observed_seq=observed_seq,
            retrieval_text=build_retrieval_text(entity),
        )
        for entity in parse_python_entities(path, source).entities
    ]


def _walk(
    node: ts.Node,
    source_bytes: bytes,
    path: str,
    *,
    parent: str | None,
    out: list[ParsedEntity],
) -> None:
    for child in node.children:
        if child.type == "class_definition":
            _emit_class(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "function_definition":
            _emit_function(child, source_bytes, path, parent=parent, out=out)
        elif child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is None:
                for sub in child.named_children:
                    if sub.type in {"class_definition", "function_definition"}:
                        inner = sub
                        break
            if inner is not None and inner.type == "class_definition":
                _emit_class(inner, source_bytes, path, parent=parent, out=out)
            elif inner is not None and inner.type == "function_definition":
                _emit_function(inner, source_bytes, path, parent=parent, out=out)


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
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="python",
            kind=EntityKind.CLASS,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=f"class {name}",
            docstring=_block_docstring(node, source_bytes),
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )
    body = node.child_by_field_name("body")
    if body is not None:
        _walk(body, source_bytes, path, parent=qualified_name, out=out)


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
    kind = EntityKind.METHOD if parent else EntityKind.FUNCTION
    out.append(
        ParsedEntity(
            entity_id=make_entity_id(path, qualified_name),
            path=path,
            language="python",
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=_python_function_signature(node, source_bytes),
            docstring=_block_docstring(node, source_bytes),
            comment="",
            body=node_text(node, source_bytes),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
        )
    )
    body = node.child_by_field_name("body")
    if body is not None:
        _walk(body, source_bytes, path, parent=qualified_name, out=out)


def _python_function_signature(func_node: ts.Node, source_bytes: bytes) -> str:
    name_node = func_node.child_by_field_name("name")
    params_node = func_node.child_by_field_name("parameters")
    if name_node is None or params_node is None:
        return "def <anonymous>()"
    name = node_text(name_node, source_bytes)
    params: list[str] = []
    for child in params_node.named_children:
        if child.type == "identifier":
            params.append(node_text(child, source_bytes))
        elif child.type in {
            "typed_parameter",
            "default_parameter",
            "typed_default_parameter",
            "list_splat_pattern",
            "dictionary_splat_pattern",
        }:
            inner = child.child_by_field_name("name")
            if inner is not None:
                params.append(node_text(inner, source_bytes))
                continue
            for sub in child.named_children:
                if sub.type == "identifier":
                    params.append(node_text(sub, source_bytes))
                    break
    rendered = ", ".join(params)
    prefix = "async def" if _is_async_function(func_node) else "def"
    return f"{prefix} {name}({rendered})"


def _is_async_function(node: ts.Node) -> bool:
    return any(child.type == "async" for child in node.children)


def _module_docstring(root: ts.Node, source_bytes: bytes) -> str:
    for child in root.named_children:
        if child.type == "expression_statement" and child.named_children:
            first = child.named_children[0]
            if first.type == "string":
                return _clean_docstring(node_text(first, source_bytes))
        break
    return ""


def _block_docstring(def_node: ts.Node, source_bytes: bytes) -> str:
    body = def_node.child_by_field_name("body")
    if body is None:
        return ""
    for child in body.named_children:
        if child.type == "expression_statement" and child.named_children:
            first = child.named_children[0]
            if first.type == "string":
                return _clean_docstring(node_text(first, source_bytes))
        break
    return ""


def _clean_docstring(raw: str) -> str:
    text = raw.strip()
    for quote in ('"""', "'''", '"', "'"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            text = text[len(quote) : -len(quote)]
            break
    return text.strip()
