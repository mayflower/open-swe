from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EntityKind(StrEnum):
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    TYPE = "type"


class RepoEventKind(StrEnum):
    EDIT = "edit"
    DECISION = "decision"
    WATCHOUT = "watchout"
    OBSERVATION = "observation"


@dataclass(slots=True)
class FileRevision:
    repo: str
    path: str
    language: str
    observed_seq: int
    content: str
    summary: str = ""


@dataclass(slots=True)
class EntityRevision:
    entity_id: str
    repo: str
    path: str
    language: str
    kind: EntityKind
    name: str
    qualified_name: str
    observed_seq: int
    signature: str = ""
    parent_qualified_name: str | None = None
    docstring: str = ""
    comment: str = ""
    body: str = ""
    retrieval_text: str = ""
    start_line: int | None = None
    end_line: int | None = None


@dataclass(slots=True)
class RepoCoreBlock:
    label: str
    description: str
    value: str
    token_budget: int
    read_only: bool = True


@dataclass(slots=True)
class RepoEvent:
    repo: str
    event_id: str
    kind: RepoEventKind
    summary: str
    observed_seq: int
    path: str | None = None
    entity_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodeEntity:
    entity_id: str
    repo: str
    path: str
    language: str
    kind: EntityKind
    current_revision: EntityRevision
    revisions: list[EntityRevision] = field(default_factory=list)
    predecessor_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.revisions:
            self.revisions.append(self.current_revision)

    def observe(self, revision: EntityRevision) -> None:
        self.revisions.append(revision)
        if revision.observed_seq >= self.current_revision.observed_seq:
            self.current_revision = revision


@dataclass(slots=True)
class RepoFile:
    repo: str
    path: str
    language: str
    current_revision: FileRevision
    revisions: list[FileRevision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.revisions:
            self.revisions.append(self.current_revision)

    def observe(self, revision: FileRevision) -> None:
        self.revisions.append(revision)
        if revision.observed_seq >= self.current_revision.observed_seq:
            self.current_revision = revision


def make_repo_event_id(repo: str, observed_seq: int, kind: RepoEventKind) -> str:
    return f"{repo}:{kind.value}:{observed_seq}"

