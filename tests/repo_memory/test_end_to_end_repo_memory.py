import asyncio
from unittest.mock import patch

from agent.repo_memory.compiler import render_repo_memory_message
from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import RepoEventKind
from agent.repo_memory.events import remember_decision_event
from agent.repo_memory.middleware.dirty_tracking import update_state_for_tool
from agent.repo_memory.middleware.injection import inject_repo_memory_before_model
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.retrieval.search import (
    search_similar_code_results,
    search_store_similar_code_results,
)
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.repo_memory.state import create_repo_memory_state


def test_end_to_end_repo_memory_flow_uses_automatic_runtime_handoff_and_flush() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="repo", store=store, config=RepoMemoryConfig())
    runtime.sandbox_backend = _FakeBackend(
        files={"agent/feature.py": "def helper(value):\n    return value.strip()\n"}
    )
    runtime.work_dir = "/workspace"
    state = create_repo_memory_state()

    update_state_for_tool(state, tool_name="write_file", tool_args={"path": "agent/feature.py"})
    store.append_repo_event(
        remember_decision_event(
            repo="repo",
            observed_seq=2,
            summary="Prefer helper reuse before adding duplicate code.",
            path="agent/feature.py",
        )
    )

    with patch(
        "agent.repo_memory.middleware.injection.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        payload = asyncio.run(inject_repo_memory_before_model(state, runtime=object()))
    results = search_similar_code_results(
        store.iter_entities("repo"),
        "helper reuse duplicate code",
        config=runtime.config,
        current_path="agent/current.py",
        language="python",
        kind="function",
    )

    assert payload is not None
    assert state["repo_memory_runtime"] is runtime
    assert state["dirty_paths"] == set()
    assert "Repository memory" in render_repo_memory_message(store.list_core_blocks("repo"))
    assert results[0].entity.qualified_name == "helper"
    assert store.list_repo_events("repo")[0].kind == RepoEventKind.DECISION
    assert store.get_entity("agent/feature.py:helper") is not None


def test_end_to_end_repo_memory_flow_persists_and_retrieves_through_postgres(
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
    runtime.sandbox_backend = _FakeBackend(
        files={"agent/feature.py": "def helper(value):\n    return value.strip().lower()\n"}
    )
    runtime.work_dir = "/workspace"
    state = create_repo_memory_state()

    update_state_for_tool(state, tool_name="write_file", tool_args={"path": "agent/feature.py"})
    runtime.store.append_repo_event(
        remember_decision_event(
            repo="repo",
            observed_seq=2,
            summary="Prefer shared normalization helpers.",
            path="agent/feature.py",
        )
    )

    with patch(
        "agent.repo_memory.middleware.injection.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        payload = asyncio.run(inject_repo_memory_before_model(state, runtime=object()))

    reloaded_store = PostgresRepoMemoryStore(
        database_url=postgres_url,
        embedding_provider=runtime.store.embedding_provider,
    )
    results = search_store_similar_code_results(
        reloaded_store,
        "repo",
        "shared helper normalization lowercase strip",
        config=runtime.config,
        current_path="agent/current.py",
        language="python",
        kind="function",
    )

    assert payload is not None
    assert state["repo_memory_runtime"] is runtime
    assert state["dirty_paths"] == set()
    assert "Repository memory" in render_repo_memory_message(reloaded_store.list_core_blocks("repo"))
    assert results[0].entity.qualified_name == "helper"
    assert "vector=" in results[0].explanation
    assert reloaded_store.list_repo_events("repo")[0].kind == RepoEventKind.DECISION
    assert reloaded_store.get_entity("agent/feature.py:helper") is not None


class _FakeResult:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code


class _FakeBackend:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def execute(self, command: str) -> _FakeResult:
        for path, content in self.files.items():
            if f"cat '{path}'" in command or f'cat "{path}"' in command or f"cat {path}" in command:
                return _FakeResult(content)
        return _FakeResult("", exit_code=1)
