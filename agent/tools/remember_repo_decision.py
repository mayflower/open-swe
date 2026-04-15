from __future__ import annotations

from typing import Any

from ..repo_memory.events import remember_decision_event
from ..repo_memory.runtime import resolve_runtime_from_context, runtime_attr


def remember_repo_decision(
    decision: str,
    path: str | None = None,
    entity_id: str | None = None,
    evidence_refs: list[str] | None = None,
    observed_seq: int | None = None,
) -> dict[str, Any]:
    """Record a repo-scoped decision event."""
    runtime = resolve_runtime_from_context()
    repo = runtime_attr(runtime, "repo", "unknown")
    store = runtime_attr(runtime, "store")
    if store is None:
        return {"status": "unavailable", "repo": repo, "summary": decision}
    next_seq = observed_seq or store.get_sync_state(repo).get("last_observed_seq", 0) + 1
    event = remember_decision_event(
        repo=repo,
        observed_seq=next_seq,
        summary=decision,
        path=path,
        entity_id=entity_id,
        evidence_refs=evidence_refs,
    )
    store.append_repo_event(event)
    return {
        "status": "ok",
        "event_id": event.event_id,
        "repo": repo,
        "summary": event.summary,
        "path": path,
        "entity_id": entity_id,
    }
