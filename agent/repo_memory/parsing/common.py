from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain import EntityKind, EntityRevision


@dataclass(slots=True)
class ParsedEntity:
    entity_id: str
    path: str
    language: str
    kind: EntityKind
    name: str
    qualified_name: str
    parent_qualified_name: str | None
    signature: str
    docstring: str
    comment: str
    body: str
    start_line: int | None = None
    end_line: int | None = None

    def to_revision(self, repo: str, observed_seq: int, retrieval_text: str) -> EntityRevision:
        return EntityRevision(
            entity_id=scope_entity_id_to_repo(repo, self.entity_id),
            repo=repo,
            path=self.path,
            language=self.language,
            kind=self.kind,
            name=self.name,
            qualified_name=self.qualified_name,
            observed_seq=observed_seq,
            signature=self.signature,
            parent_qualified_name=self.parent_qualified_name,
            docstring=self.docstring,
            comment=self.comment,
            body=self.body,
            retrieval_text=retrieval_text,
            start_line=self.start_line,
            end_line=self.end_line,
        )


def normalize_body(body: str) -> str:
    return re.sub(r"\s+", " ", body.strip())


def make_qualified_name(parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


_REPO_ID_SEPARATOR = "|"


def make_entity_id(path: str, qualified_name: str) -> str:
    """Path-scoped entity id used at parse time.

    Repo scoping is applied later in :func:`scope_entity_id_to_repo` (called
    from :meth:`ParsedEntity.to_revision`) so parsers stay independent of
    the repo concept.
    """
    return f"{path}:{qualified_name}"


def scope_entity_id_to_repo(repo: str, entity_id: str) -> str:
    """Apply the repo prefix to a path-scoped entity id.

    Without this, two repositories that happen to share a
    ``path:qualified_name`` (e.g., both have ``src/main.py:main``) would
    collide on ``entities.entity_id PRIMARY KEY`` and one repo would
    silently overwrite the other's revisions / lineage. Idempotent: an
    already-scoped id is returned unchanged so callers don't have to check.
    """
    if entity_id.startswith(f"{repo}{_REPO_ID_SEPARATOR}"):
        return entity_id
    return f"{repo}{_REPO_ID_SEPARATOR}{entity_id}"


def unscope_entity_id(repo: str, entity_id: str) -> str:
    """Strip the repo prefix from a scoped entity id (debugging / display)."""
    prefix = f"{repo}{_REPO_ID_SEPARATOR}"
    if entity_id.startswith(prefix):
        return entity_id[len(prefix) :]
    return entity_id
