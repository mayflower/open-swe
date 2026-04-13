from agent.repo_memory.domain import (
    EntityKind,
    EntityRevision,
    FileRevision,
    RepoEvent,
    RepoEventKind,
)
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore


def test_repository_updates_current_while_preserving_history() -> None:
    store = InMemoryRepoMemoryStore()
    store.upsert_file_revision(
        FileRevision(
            repo="repo",
            path="a.py",
            language="python",
            observed_seq=1,
            content="print(1)",
        )
    )
    store.upsert_file_revision(
        FileRevision(
            repo="repo",
            path="a.py",
            language="python",
            observed_seq=2,
            content="print(2)",
        )
    )

    repo_file = store.get_file("repo", "a.py")

    assert repo_file is not None
    assert repo_file.current_revision.observed_seq == 2
    assert [revision.observed_seq for revision in repo_file.revisions] == [1, 2]


def test_repo_events_append_without_overwrite() -> None:
    store = InMemoryRepoMemoryStore()
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.EDIT,
            summary="edited",
            observed_seq=1,
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="2",
            kind=RepoEventKind.DECISION,
            summary="decided",
            observed_seq=2,
        )
    )

    assert [event.event_id for event in store.list_repo_events("repo")] == ["1", "2"]


def test_entities_keep_current_revision_pointer() -> None:
    store = InMemoryRepoMemoryStore()
    first = EntityRevision(
        entity_id="entity",
        repo="repo",
        path="a.py",
        language="python",
        kind=EntityKind.FUNCTION,
        name="helper",
        qualified_name="helper",
        observed_seq=1,
        retrieval_text="helper body",
    )
    second = EntityRevision(
        entity_id="entity",
        repo="repo",
        path="a.py",
        language="python",
        kind=EntityKind.FUNCTION,
        name="helper",
        qualified_name="helper",
        observed_seq=3,
        retrieval_text="helper body changed",
    )

    store.upsert_entity_revision(first)
    store.upsert_entity_revision(second)

    entity = store.get_entity("entity")
    assert entity is not None
    assert entity.current_revision.observed_seq == 3
    assert [revision.observed_seq for revision in entity.revisions] == [1, 3]
