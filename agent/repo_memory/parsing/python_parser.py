from __future__ import annotations

import ast
from dataclasses import dataclass

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name
from .retrieval_text import build_retrieval_text


@dataclass(slots=True)
class PythonParseResult:
    entities: list[ParsedEntity]


class _EntityCollector(ast.NodeVisitor):
    def __init__(self, source: str, path: str) -> None:
        self.source = source
        self.path = path
        self.entities: list[ParsedEntity] = []
        self.parents: list[str] = []

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        module_name = self.path.rsplit("/", 1)[-1]
        entity = ParsedEntity(
            entity_id=make_entity_id(self.path, module_name),
            path=self.path,
            language="python",
            kind=EntityKind.MODULE,
            name=module_name,
            qualified_name=module_name,
            parent_qualified_name=None,
            signature="module",
            docstring=ast.get_docstring(node) or "",
            comment="",
            body=self.source,
            start_line=1,
            end_line=len(self.source.splitlines()),
        )
        self.entities.append(entity)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._add_entity(node, EntityKind.CLASS, f"class {node.name}")
        qualified_parent = (
            make_qualified_name(self.parents[-1], node.name) if self.parents else node.name
        )
        self.parents.append(qualified_parent)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        kind = EntityKind.METHOD if self.parents else EntityKind.FUNCTION
        signature = _function_signature(node)
        self._add_entity(node, kind, signature)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        kind = EntityKind.METHOD if self.parents else EntityKind.FUNCTION
        signature = f"async {_function_signature(node)}"
        self._add_entity(node, kind, signature)

    def _add_entity(self, node: ast.AST, kind: EntityKind, signature: str) -> None:
        name = getattr(node, "name")
        parent = self.parents[-1] if self.parents else None
        qualified_name = make_qualified_name(parent, name)
        body = ast.get_source_segment(self.source, node) or ""
        entity = ParsedEntity(
            entity_id=make_entity_id(self.path, qualified_name),
            path=self.path,
            language="python",
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent,
            signature=signature,
            docstring=ast.get_docstring(node) or "",
            comment="",
            body=body,
            start_line=getattr(node, "lineno", None),
            end_line=getattr(node, "end_lineno", None),
        )
        self.entities.append(entity)


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    for arg in node.args.args:
        args.append(arg.arg)
    rendered = ", ".join(args)
    return f"def {node.name}({rendered})"


def parse_python_entities(path: str, source: str) -> PythonParseResult:
    tree = ast.parse(source)
    collector = _EntityCollector(source, path)
    collector.visit(tree)
    return PythonParseResult(entities=collector.entities)


def parse_python_revisions(repo: str, path: str, source: str, observed_seq: int) -> list:
    return [
        entity.to_revision(
            repo=repo,
            observed_seq=observed_seq,
            retrieval_text=build_retrieval_text(entity),
        )
        for entity in parse_python_entities(path, source).entities
    ]
