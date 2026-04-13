from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FocusSet:
    paths: list[str]
    entities: list[str]


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def compute_focus_set(
    explicit_paths: list[str] | None = None,
    explicit_entities: list[str] | None = None,
    derived_paths: list[str] | None = None,
    derived_entities: list[str] | None = None,
) -> FocusSet:
    paths = _unique_ordered((explicit_paths or []) + (derived_paths or []))
    entities = _unique_ordered((explicit_entities or []) + (derived_entities or []))
    return FocusSet(paths=paths, entities=entities)

