from unittest.mock import patch

from agent.repo_memory.domain import EntityKind, EntityRevision, RepoEvent, RepoEventKind
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.tools.get_entity_history import get_entity_history


def test_get_entity_history_returns_recent_events_and_identity() -> None:
    runtime = RepoMemoryRuntime(repo="repo")
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="entity:helper",
            repo="repo",
            path="agent/a.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="helper",
            qualified_name="helper",
            observed_seq=7,
            retrieval_text="helper body",
        )
    )
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="helper should stay reusable",
            observed_seq=8,
            entity_id="entity:helper",
        )
    )
    with patch(
        "agent.tools.get_entity_history.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = get_entity_history("entity:helper")

    assert result["status"] == "ok"
    assert result["qualified_name"] == "helper"
    assert result["recent_events"] == ["helper should stay reusable"]
    assert result["provenance"]["last_observed_seq"] == 7
