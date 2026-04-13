from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ColumnSpec:
    name: str
    type_name: str
    nullable: bool = False


@dataclass(slots=True)
class TableSpec:
    name: str
    columns: dict[str, ColumnSpec] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryMetadata:
    tables: dict[str, TableSpec]


def _table(name: str, *columns: ColumnSpec) -> TableSpec:
    return TableSpec(name=name, columns={column.name: column for column in columns})


def build_metadata() -> MemoryMetadata:
    tables = {
        "repositories": _table(
            "repositories",
            ColumnSpec("repo", "text"),
        ),
        "files": _table(
            "files",
            ColumnSpec("repo", "text"),
            ColumnSpec("path", "text"),
            ColumnSpec("current_observed_seq", "int"),
        ),
        "file_revisions": _table(
            "file_revisions",
            ColumnSpec("repo", "text"),
            ColumnSpec("path", "text"),
            ColumnSpec("observed_seq", "int"),
            ColumnSpec("content", "text"),
        ),
        "entities": _table(
            "entities",
            ColumnSpec("entity_id", "text"),
            ColumnSpec("repo", "text"),
            ColumnSpec("path", "text"),
            ColumnSpec("current_observed_seq", "int"),
        ),
        "entity_revisions": _table(
            "entity_revisions",
            ColumnSpec("entity_id", "text"),
            ColumnSpec("observed_seq", "int"),
            ColumnSpec("qualified_name", "text"),
            ColumnSpec("retrieval_text", "text"),
            ColumnSpec("embedding", "vector", nullable=True),
        ),
        "entity_links": _table(
            "entity_links",
            ColumnSpec("entity_id", "text"),
            ColumnSpec("related_entity_id", "text"),
            ColumnSpec("link_type", "text"),
        ),
        "repo_events": _table(
            "repo_events",
            ColumnSpec("event_id", "text"),
            ColumnSpec("repo", "text"),
            ColumnSpec("kind", "text"),
            ColumnSpec("observed_seq", "int"),
        ),
        "repo_core_blocks": _table(
            "repo_core_blocks",
            ColumnSpec("repo", "text"),
            ColumnSpec("label", "text"),
            ColumnSpec("value", "text"),
        ),
        "sync_state": _table(
            "sync_state",
            ColumnSpec("repo", "text"),
            ColumnSpec("last_observed_seq", "int"),
            ColumnSpec("last_compiled_seq", "int"),
        ),
    }
    return MemoryMetadata(tables=tables)

