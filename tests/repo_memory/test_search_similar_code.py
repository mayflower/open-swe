from unittest.mock import patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import EntityKind, EntityRevision
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
        "agent.tools.search_similar_code.get_config",
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
