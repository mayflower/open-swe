from agent.repo_memory.domain import RepoEvent, RepoEventKind
from agent.repo_memory.events import search_repo_events
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore


def test_repo_event_search_prefers_scoped_matches() -> None:
    store = InMemoryRepoMemoryStore()
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Use middleware injection for repo memory",
            observed_seq=1,
            path="agent/server.py",
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="2",
            kind=RepoEventKind.OBSERVATION,
            summary="Prompt assembly happens in agent/prompt.py",
            observed_seq=2,
            path="agent/prompt.py",
        )
    )

    results = search_repo_events(
        store.list_repo_events("repo"),
        "repo memory injection",
        path="agent/server.py",
    )

    assert results[0].event.event_id == "1"
    assert "same path" in results[0].explanation
