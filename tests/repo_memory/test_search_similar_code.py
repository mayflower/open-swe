from unittest.mock import patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import EntityKind, EntityRevision
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.tools.search_similar_code import search_similar_code


def test_search_similar_code_excludes_current_file_and_prefers_same_language() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="a",
            repo="repo",
            path="agent/a.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="helper",
            qualified_name="helper",
            observed_seq=5,
            retrieval_text="helper reuse normalization python",
        )
    )
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="b",
            repo="repo",
            path="agent/b.ts",
            language="typescript",
            kind=EntityKind.FUNCTION,
            name="helperTs",
            qualified_name="helperTs",
            observed_seq=3,
            retrieval_text="helper reuse normalization typescript",
        )
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = search_similar_code(
            "reuse normalization helper",
            current_path="agent/current.py",
            language="python",
            kind="function",
        )

    assert result["results"][0]["entity_id"] == "a"
    assert "same language" in result["results"][0]["explanation"]


def test_search_similar_code_uses_pgvector_ranking_when_postgres_store_is_available(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=postgres_store,
        config=RepoMemoryConfig(
            backend="postgres",
            database_url=postgres_url,
            embedding_provider="hashed",
            embedding_dimensions=16,
        ),
    )
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="py",
            repo="repo",
            path="agent/helpers.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="normalize_name",
            qualified_name="normalize_name",
            observed_seq=5,
            retrieval_text="normalize helper reuse shared helper strip lowercase",
        )
    )
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="ts",
            repo="repo",
            path="agent/helpers.ts",
            language="typescript",
            kind=EntityKind.FUNCTION,
            name="normalizeLabel",
            qualified_name="normalizeLabel",
            observed_seq=4,
            retrieval_text="label formatter ui rendering browser text",
        )
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = search_similar_code(
            "shared helper normalize lowercase strip",
            current_path="agent/current.py",
            language="python",
            kind="function",
        )

    assert result["results"][0]["entity_id"] == "py"
    assert "vector=" in result["results"][0]["explanation"]


def test_search_similar_code_pgvector_respects_current_path_and_entity_filters(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=postgres_store,
        config=RepoMemoryConfig(
            backend="postgres",
            database_url=postgres_url,
            embedding_provider="hashed",
            embedding_dimensions=16,
        ),
    )
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="current",
            repo="repo",
            path="agent/current.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="normalize_current",
            qualified_name="normalize_current",
            observed_seq=6,
            retrieval_text="shared normalize lowercase strip helper",
        )
    )
    runtime.store.upsert_entity_revision(
        EntityRevision(
            entity_id="alt",
            repo="repo",
            path="agent/alt.py",
            language="python",
            kind=EntityKind.FUNCTION,
            name="normalize_alt",
            qualified_name="normalize_alt",
            observed_seq=5,
            retrieval_text="shared normalize lowercase strip helper reusable",
        )
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = search_similar_code(
            "shared normalize lowercase strip helper",
            current_path="agent/current.py",
            current_entity_id="current",
            language="python",
            kind="function",
        )

    assert [item["entity_id"] for item in result["results"]] == ["alt"]
