from __future__ import annotations

import re

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id


TYPE_PATTERN = re.compile(r"pub\s+(struct|enum|trait)\s+([A-Za-z0-9_]+)")
FN_PATTERN = re.compile(r"pub\s+fn\s+([A-Za-z0-9_]+)\((.*?)\)")


def parse_rust_entities(path: str, source: str) -> list[ParsedEntity]:
    entities: list[ParsedEntity] = []
    current_parent: str | None = None
    for idx, line in enumerate(source.splitlines(), start=1):
        if current_parent and line.strip() == "}":
            current_parent = None
            continue
        type_match = TYPE_PATTERN.search(line)
        if type_match:
            kind_name, name = type_match.groups()
            kind = {
                "struct": EntityKind.STRUCT,
                "enum": EntityKind.ENUM,
                "trait": EntityKind.TRAIT,
            }[kind_name]
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, name),
                    path=path,
                    language="rust",
                    kind=kind,
                    name=name,
                    qualified_name=name,
                    parent_qualified_name=None,
                    signature=line.strip(),
                    docstring="",
                    comment="",
                    body=line.strip(),
                    start_line=idx,
                    end_line=idx,
                )
            )
            current_parent = name if kind == EntityKind.TRAIT and "}" not in line else None
            continue
        fn_match = FN_PATTERN.search(line)
        if fn_match:
            name, args = fn_match.groups()
            qualified_name = f"{current_parent}.{name}" if current_parent else name
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, qualified_name),
                    path=path,
                    language="rust",
                    kind=EntityKind.METHOD if current_parent else EntityKind.FUNCTION,
                    name=name,
                    qualified_name=qualified_name,
                    parent_qualified_name=current_parent,
                    signature=f"fn {name}({args})",
                    docstring="",
                    comment="",
                    body=line.strip(),
                    start_line=idx,
                    end_line=idx,
                )
            )
    return entities
