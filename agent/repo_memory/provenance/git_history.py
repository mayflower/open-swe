from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(slots=True)
class BlameRecord:
    commit: str
    author: str
    summary: str


def aggregate_blame(records: list[BlameRecord]) -> dict[str, object]:
    authors = Counter(record.author for record in records)
    commits = [record.commit for record in records]
    summaries = [record.summary for record in records]
    return {
        "top_authors": authors.most_common(3),
        "last_commit": commits[0] if commits else None,
        "summaries": summaries[:3],
    }


def maybe_load_deep_history(enabled: bool, loader) -> list[dict]:
    return loader() if enabled else []

