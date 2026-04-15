from __future__ import annotations

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import (
    EntityKind,
    EntityRevision,
    FileRevision,
    RepoCoreBlock,
    RepoEvent,
    RepoEventKind,
)
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import create_repo_memory_store


def test_postgres_store_persists_files_entities_events_and_sync_state(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    postgres_store.upsert_file_revision(
        FileRevision(
            repo="repo",
            path="agent/a.py",
            language="python",
            observed_seq=1,
            content="def helper():\n    return 1\n",
        )
    )
    postgres_store.upsert_entity_revision(
        EntityRevision(
            entity_id="agent/a.py:helper",
            repo="repo",
            path="agent/a.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="helper",
            qualified_name="helper",
            observed_seq=1,
            retrieval_text="helper reuse normalization",
        )
    )
    postgres_store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="event-1",
            kind=RepoEventKind.DECISION,
            summary="Prefer helper reuse.",
            observed_seq=2,
            path="agent/a.py",
            entity_id="agent/a.py:helper",
        )
    )
    postgres_store.set_core_block(
        "repo",
        RepoCoreBlock(
            label="active_design_decisions",
            description="Most recent decisions",
            value="Prefer helper reuse.",
            token_budget=120,
        ),
    )
    postgres_store.set_last_compiled_seq("repo", 2)

    reloaded = create_repo_memory_store(
        RepoMemoryConfig(
            backend="postgres",
            database_url=postgres_url,
            embedding_provider="hashed",
            embedding_dimensions=16,
        )
    )
    assert isinstance(reloaded, PostgresRepoMemoryStore)

    repo_file = reloaded.get_file("repo", "agent/a.py")
    entity = reloaded.get_entity("agent/a.py:helper")

    assert repo_file is not None
    assert repo_file.current_revision.observed_seq == 1
    assert entity is not None
    assert entity.current_revision.qualified_name == "helper"
    assert reloaded.list_repositories() == ["repo"]
    assert [event.event_id for event in reloaded.list_repo_events("repo")] == ["event-1"]
    assert reloaded.list_core_blocks("repo")[0].label == "active_design_decisions"
    assert reloaded.get_sync_state("repo") == {
        "last_observed_seq": 2,
        "last_compiled_seq": 2,
        "dreaming_cursor": 0,
    }


def test_postgres_store_records_lineage(postgres_store: PostgresRepoMemoryStore) -> None:
    postgres_store.upsert_entity_revision(
        EntityRevision(
            entity_id="entity:new",
            repo="repo",
            path="agent/a.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="new_helper",
            qualified_name="new_helper",
            observed_seq=3,
            retrieval_text="new helper extraction",
        )
    )

    postgres_store.record_lineage("entity:new", "entity:old", "rename", 0.91)

    entity = postgres_store.get_entity("entity:new")

    assert entity is not None
    assert entity.predecessor_ids == ["entity:old"]
    assert postgres_store.list_lineage() == [
        {
            "entity_id": "entity:new",
            "predecessor_id": "entity:old",
            "reason": "rename",
            "confidence": 0.91,
        }
    ]
