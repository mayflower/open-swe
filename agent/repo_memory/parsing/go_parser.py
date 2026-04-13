from __future__ import annotations

import re

from ..domain import EntityKind
from .common import ParsedEntity, make_entity_id


TYPE_PATTERN = re.compile(r"type\s+([A-Za-z0-9_]+)\s+(struct|interface)")
FUNC_PATTERN = re.compile(r"func\s+(?:\((.*?)\)\s+)?([A-Za-z0-9_]+)\((.*?)\)")


def parse_go_entities(path: str, source: str) -> list[ParsedEntity]:
    entities: list[ParsedEntity] = []
    for idx, line in enumerate(source.splitlines(), start=1):
        type_match = TYPE_PATTERN.search(line)
        if type_match:
            name, kind_name = type_match.groups()
            kind = EntityKind.STRUCT if kind_name == "struct" else EntityKind.INTERFACE
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, name),
                    path=path,
                    language="go",
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
            continue
        func_match = FUNC_PATTERN.search(line)
        if func_match:
            receiver, name, args = func_match.groups()
            parent = None
            kind = EntityKind.FUNCTION
            qualified_name = name
            if receiver:
                receiver_name = receiver.split()[-1].lstrip("*")
                parent = receiver_name
                kind = EntityKind.METHOD
                qualified_name = f"{receiver_name}.{name}"
            entities.append(
                ParsedEntity(
                    entity_id=make_entity_id(path, qualified_name),
                    path=path,
                    language="go",
                    kind=kind,
                    name=name,
                    qualified_name=qualified_name,
                    parent_qualified_name=parent,
                    signature=f"func {name}({args})",
                    docstring="",
                    comment="",
                    body=line.strip(),
                    start_line=idx,
                    end_line=idx,
                )
            )
    return entities

