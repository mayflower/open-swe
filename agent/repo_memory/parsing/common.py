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
            entity_id=self.entity_id,
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


def make_entity_id(path: str, qualified_name: str) -> str:
    return f"{path}:{qualified_name}"

