from agent.repo_memory.compiler import render_repo_memory_message
from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import RepoEventKind
from agent.repo_memory.events import remember_decision_event
from agent.repo_memory.middleware.dirty_tracking import update_state_for_tool
from agent.repo_memory.middleware.injection import build_injection_payload
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.repo_memory.sync import FlushCoordinator
from agent.repo_memory.state import create_repo_memory_state
from agent.repo_memory.retrieval.search import search_similar_code_results


def test_end_to_end_repo_memory_flow() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="repo", store=store, config=RepoMemoryConfig())
    state = create_repo_memory_state()
    state["repo_memory_runtime"] = {
        "repo": "repo",
        "store": store,
        "config": runtime.config,
    }

    update_state_for_tool(state, tool_name="write_file", tool_args={"path": "agent/feature.py"})
    coordinator = FlushCoordinator(repo="repo", store=store)
    flushed = coordinator.flush(
        changed_files={"agent/feature.py": "def helper(value):\n    return value.strip()\n"},
        observed_seq=3,
        focus_paths=state["focus_paths"],
    )
    store.append_repo_event(
        remember_decision_event(
            repo="repo",
            observed_seq=4,
            summary="Prefer helper reuse before adding duplicate code.",
            path="agent/feature.py",
        )
    )

    payload = build_injection_payload(state)
    results = search_similar_code_results(
        store.iter_entities("repo"),
        "helper reuse duplicate code",
        config=runtime.config,
        current_path="agent/current.py",
        language="python",
        kind="function",
    )

    assert flushed == ["agent/feature.py"]
    assert payload is not None
    assert "Repository memory" in render_repo_memory_message(store.list_core_blocks("repo"))
    assert results[0].entity.qualified_name == "helper"
    assert store.list_repo_events("repo")[0].kind == RepoEventKind.DECISION
