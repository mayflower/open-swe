from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


class ClaimScopeKind(StrEnum):
    REPO = "repo"
    PATH = "path"
    ENTITY = "entity"


class ClaimKind(StrEnum):
    DESIGN_DECISION = "design_decision"
    WATCHOUT = "watchout"
    HIGH_IMPACT_CHANGE = "high_impact_change"
    REUSE_HINT = "reuse_hint"


class ClaimStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONTESTED = "contested"
    STALE = "stale"
    PROMOTED = "promoted"
    ARCHIVED = "archived"


class RevalidationMode(StrEnum):
    STRICT_LIVE_STATE = "strict_live_state"
    EVIDENCE_ONLY = "evidence_only"
    MANUAL_REVIEW = "manual_review"


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
class MemoryClaim:
    claim_id: str
    claim_key: str
    source_identity_key: str
    repo: str
    scope_kind: ClaimScopeKind
    scope_ref: str
    claim_kind: ClaimKind
    text: str
    normalized_text: str
    status: ClaimStatus
    score: float = 0.0
    score_components: dict[str, float] = field(default_factory=dict)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_revalidated_at: datetime | None = None
    revalidation_mode: RevalidationMode = RevalidationMode.EVIDENCE_ONLY
    embedding: list[float] = field(default_factory=list)
    embedding_provider: str = "openai"
    embedding_dimensions: int = 1536
    embedding_version: str = "text-embedding-3-small:1536"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ClaimEvidence:
    evidence_id: str
    repo: str
    claim_key: str
    run_id: str | None
    evidence_kind: str
    evidence_ref: str
    evidence_text: str
    weight: float
    observed_at: datetime
    source_thread_id: str | None = None
    source_path: str | None = None
    source_entity_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RepoCoreSnapshot:
    snapshot_id: str
    repo: str
    compiled_at: datetime
    source_watermark: int
    blocks: list[RepoCoreBlock]
    source_claim_keys: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DreamRun:
    run_id: str
    repo: str
    run_kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    worker_id: str | None = None
    cursor_before: int = 0
    cursor_after: int = 0
    signal_count: int = 0
    claim_candidate_count: int = 0
    merged_count: int = 0
    promoted_count: int = 0
    snapshot_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DreamingLease:
    repo: str
    worker_id: str
    expires_at: datetime


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
