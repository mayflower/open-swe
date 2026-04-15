from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import (
    CodeEntity,
    EntityKind,
    EntityRevision,
    FileRevision,
    RepoCoreBlock,
    RepoEvent,
    RepoEventKind,
)
from agent.repo_memory.state import create_repo_memory_state


def test_current_revision_prefers_highest_observed_seq() -> None:
    first = EntityRevision(
        entity_id="a",
        repo="repo",
        path="agent/file.py",
        language="python",
        kind=EntityKind.FUNCTION,
        name="helper",
        qualified_name="helper",
        observed_seq=1,
    )
    second = EntityRevision(
        entity_id="a",
        repo="repo",
        path="agent/file.py",
        language="python",
        kind=EntityKind.FUNCTION,
        name="helper",
        qualified_name="helper",
        observed_seq=3,
    )

    entity = CodeEntity(
        entity_id="a",
        repo="repo",
        path="agent/file.py",
        language="python",
        kind=EntityKind.FUNCTION,
        current_revision=first,
    )
    entity.observe(second)

    assert entity.current_revision.observed_seq == 3
    assert [revision.observed_seq for revision in entity.revisions] == [1, 3]


def test_core_block_shape_and_repo_state_defaults() -> None:
    block = RepoCoreBlock(
        label="repo_rules",
        description="Rules",
        value="Keep patches small.",
        token_budget=100,
    )
    state = create_repo_memory_state()
    config = RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16)

    assert block.read_only is True
    assert state["dirty_paths"] == set()
    assert state["dirty_unknown"] is False
    assert state["focus_paths"] == []
    assert state["focus_entities"] == []
    assert state["last_compiled_seq"] == 0
    assert config.repo_scope_only is True
    assert config.core_block_token_budgets["repo_rules"] > 0


def test_events_are_append_only_contracts() -> None:
    first = RepoEvent(
        repo="repo",
        event_id="repo:decision:1",
        kind=RepoEventKind.DECISION,
        summary="Use middleware injection.",
        observed_seq=1,
    )
    second = RepoEvent(
        repo="repo",
        event_id="repo:decision:2",
        kind=RepoEventKind.DECISION,
        summary="Keep exact tool output untouched.",
        observed_seq=2,
    )
    assert [first.event_id, second.event_id] == ["repo:decision:1", "repo:decision:2"]


def test_file_revision_contract_is_repo_scoped() -> None:
    revision = FileRevision(
        repo="langchain-ai/open-swe",
        path="agent/server.py",
        language="python",
        observed_seq=4,
        content="print('hello')",
    )
    assert revision.repo == "langchain-ai/open-swe"
    assert revision.path == "agent/server.py"
