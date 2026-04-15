from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import shlex
from typing import Any

from ..runtime import runtime_attr


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


def load_entity_git_history(runtime: object, path: str, limit: int = 5) -> list[dict[str, str]]:
    backend = runtime_attr(runtime, "sandbox_backend")
    work_dir = runtime_attr(runtime, "work_dir")
    if backend is None or not work_dir or not path:
        return []

    safe_work_dir = shlex.quote(work_dir)
    safe_path = shlex.quote(path)
    command = (
        f"cd {safe_work_dir} && "
        f"git log --follow --format='%H%x09%an%x09%s' -n {int(limit)} -- {safe_path}"
    )
    result = backend.execute(command)
    if result.exit_code != 0:
        return []

    history: list[dict[str, str]] = []
    for line in result.output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        commit, author, summary = parts
        history.append(
            {
                "commit": commit,
                "author": author,
                "summary": summary,
            }
        )
    return history
