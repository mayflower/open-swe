from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.sync import FlushCoordinator


def test_flush_reparses_only_dirty_files_and_prioritizes_focus_paths() -> None:
    store = InMemoryRepoMemoryStore()
    coordinator = FlushCoordinator(repo="repo", store=store)
    changed_files = {
        "agent/other.py": "def alpha():\n    return 1\n",
        "agent/focused.py": "def beta():\n    return 2\n",
    }

    flushed = coordinator.flush(
        changed_files=changed_files,
        observed_seq=4,
        focus_paths=["agent/focused.py"],
    )

    assert flushed == ["agent/focused.py", "agent/other.py"]
    assert store.get_sync_state("repo")["last_compiled_seq"] == 4
    assert store.get_file("repo", "agent/focused.py") is not None
