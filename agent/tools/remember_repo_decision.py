from __future__ import annotations

from typing import Any

from langgraph.config import get_config

from ..repo_memory.events import remember_decision_event
from ..repo_memory.runtime import DEFAULT_RUNTIME


def remember_repo_decision(
    decision: str,
    path: str | None = None,
    entity_id: str | None = None,
    evidence_refs: list[str] | None = None,
    observed_seq: int | None = None,
) -> dict[str, Any]:
    """Record a repo-scoped decision event."""
    config = get_config()
    metadata = config.get("metadata", {})
    runtime = metadata.get("repo_memory_runtime", DEFAULT_RUNTIME)
    repo = getattr(runtime, "repo", None) or metadata.get("repo_full_name", "unknown")
    next_seq = observed_seq or runtime.store.get_sync_state(repo).get("last_observed_seq", 0) + 1
    event = remember_decision_event(
        repo=repo,
        observed_seq=next_seq,
        summary=decision,
        path=path,
        entity_id=entity_id,
        evidence_refs=evidence_refs,
    )
    runtime.store.append_repo_event(event)
    return {
        "status": "ok",
        "event_id": event.event_id,
        "repo": repo,
        "summary": event.summary,
        "path": path,
        "entity_id": entity_id,
    }

