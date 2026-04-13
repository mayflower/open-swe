from unittest.mock import patch

from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.tools.remember_repo_decision import remember_repo_decision


def test_remember_repo_decision_writes_scoped_event() -> None:
    runtime = RepoMemoryRuntime(repo="repo")
    with patch(
        "agent.tools.remember_repo_decision.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = remember_repo_decision(
            "Keep memory outside exact tool output.",
            path="agent/server.py",
            entity_id="agent/server.py:get_agent",
            evidence_refs=["docs/repo_memory_codex_discovery.md"],
        )

    assert result["status"] == "ok"
    [event] = runtime.store.list_repo_events("repo")
    assert event.path == "agent/server.py"
    assert event.entity_id == "agent/server.py:get_agent"
    assert event.evidence_refs == ["docs/repo_memory_codex_discovery.md"]
