from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import RepoEvent, RepoEventKind
from agent.repo_memory.middleware.injection import build_injection_payload
from agent.repo_memory.runtime import RepoMemoryRuntime


def test_injection_builds_separate_repo_memory_message() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Use middleware injection.",
            observed_seq=1,
        )
    )
    state = {
        "focus_paths": [],
        "focus_entities": [],
        "repo_memory_runtime": {
            "repo": "repo",
            "store": runtime.store,
            "config": runtime.config,
        },
    }

    payload = build_injection_payload(state)

    assert payload is not None
    assert payload["messages"][0]["role"] == "system"
    assert "Repository memory" in payload["messages"][0]["content"][0]["text"]
