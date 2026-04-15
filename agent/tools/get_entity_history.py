from __future__ import annotations

from typing import Any

from ..repo_memory.events import search_repo_events
from ..repo_memory.provenance.git_history import load_entity_git_history, maybe_load_deep_history
from ..repo_memory.provenance.summary import summarize_entity_history
from ..repo_memory.runtime import resolve_runtime_from_context, runtime_attr


def get_entity_history(
    entity_id: str,
    include_deep_history: bool = False,
) -> dict[str, Any]:
    """Return entity history from repo memory and provenance."""
    runtime = resolve_runtime_from_context()
    repo = runtime_attr(runtime, "repo", "unknown")
    store = runtime_attr(runtime, "store")
    if store is None:
        return {"status": "unavailable", "entity_id": entity_id}
    entity = store.get_entity(entity_id)
    if entity is None:
        return {"status": "not_found", "entity_id": entity_id}
    events = search_repo_events(
        store.list_repo_events(repo),
        entity.current_revision.qualified_name,
    )
    deep_history = maybe_load_deep_history(
        include_deep_history,
        lambda: load_entity_git_history(runtime, entity.current_revision.path),
    )
    payload = summarize_entity_history(
        qualified_name=entity.current_revision.qualified_name,
        recent_events=[item.event.summary for item in events],
        provenance={
            "last_observed_seq": entity.current_revision.observed_seq,
            "path": entity.current_revision.path,
        },
        deep_history=deep_history,
    )
    payload["status"] = "ok"
    return payload
