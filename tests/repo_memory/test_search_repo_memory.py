from agent.repo_memory.domain import RepoEvent, RepoEventKind
from agent.repo_memory.events import search_repo_events


def test_search_repo_memory_returns_ranked_events() -> None:
    events = [
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Prefer reuse before duplication",
            observed_seq=1,
            entity_id="entity:a",
        ),
        RepoEvent(
            repo="repo",
            event_id="2",
            kind=RepoEventKind.EDIT,
            summary="Changed unrelated webhook auth",
            observed_seq=2,
        ),
    ]

    results = search_repo_events(events, "reuse duplication", entity_id="entity:a")

    assert [result.event.event_id for result in results] == ["1"]
