from __future__ import annotations


def summarize_entity_history(
    *,
    qualified_name: str,
    recent_events: list[str],
    provenance: dict[str, object],
    deep_history: list[dict] | None = None,
) -> dict[str, object]:
    return {
        "qualified_name": qualified_name,
        "recent_events": recent_events,
        "provenance": provenance,
        "deep_history": list(deep_history or []),
    }

