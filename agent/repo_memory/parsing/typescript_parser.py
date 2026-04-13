from __future__ import annotations

import re

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id, make_qualified_name


TYPE_PATTERN = re.compile(r"export\s+(class|interface|function)\s+([A-Za-z0-9_]+)")
METHOD_PATTERN = re.compile(r"^\s+([A-Za-z0-9_]+)\((.*?)\)\s*\{", re.MULTILINE)


def parse_typescript_entities(path: str, source: str) -> list[ParsedEntity]:
    entities: list[ParsedEntity] = []
    current_parent: tuple[str, str] | None = None
    lines = source.splitlines()
    for idx, line in enumerate(lines, start=1):
        type_match = TYPE_PATTERN.search(line)
        if type_match:
            kind_name, name = type_match.groups()
            kind = {
                "class": EntityKind.CLASS,
                "interface": EntityKind.INTERFACE,
                "function": EntityKind.FUNCTION,
            }[kind_name]
            qualified_name = name
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, qualified_name),
                    path=path,
                    language="typescript",
                    kind=kind,
                    name=name,
                    qualified_name=qualified_name,
                    parent_qualified_name=None,
                    signature=line.strip(),
                    docstring="",
                    comment="",
                    body=line.strip(),
                    start_line=idx,
                    end_line=idx,
                )
            )
            current_parent = (name, kind_name) if kind_name in {"class", "interface"} else None
            continue
        method_match = METHOD_PATTERN.search(line)
        if method_match and current_parent:
            name, args = method_match.groups()
            qualified_name = make_qualified_name(current_parent[0], name)
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, qualified_name),
                    path=path,
                    language="typescript",
                    kind=EntityKind.METHOD,
                    name=name,
                    qualified_name=qualified_name,
                    parent_qualified_name=current_parent[0],
                    signature=f"{name}({args})",
                    docstring="",
                    comment="",
                    body=line.strip(),
                    start_line=idx,
                    end_line=idx,
                )
            )
    return entities

