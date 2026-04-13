from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentState


class RepoMemoryState(AgentState):
    dirty_paths: set[str]
    dirty_unknown: bool
    focus_paths: list[str]
    focus_entities: list[str]
    last_compiled_seq: int
    repo_memory_runtime: dict[str, Any]


def create_repo_memory_state() -> dict[str, Any]:
    return {
        "dirty_paths": set(),
        "dirty_unknown": False,
        "focus_paths": [],
        "focus_entities": [],
        "last_compiled_seq": 0,
        "repo_memory_runtime": {},
    }

