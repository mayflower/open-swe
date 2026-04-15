from unittest.mock import patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import RepoEvent, RepoEventKind
from agent.repo_memory.middleware.injection import build_injection_payload
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime


def test_injection_builds_separate_repo_memory_message() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Use middleware injection.",
            observed_seq=1,
        )
    )
    state = {
        "focus_paths": [],
        "focus_entities": [],
        "repo_memory_runtime": runtime,
    }

    payload = build_injection_payload(state)

    assert payload is not None
    assert payload["messages"][0]["role"] == "system"
    assert "Repository memory" in payload["messages"][0]["content"][0]["text"]


def test_injection_can_resolve_runtime_from_config_metadata() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Use shared runtime resolution.",
            observed_seq=1,
        )
    )
    state = {
        "focus_paths": [],
        "focus_entities": [],
    }

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        payload = build_injection_payload(state)

    assert payload is not None
    assert state["repo_memory_runtime"] is runtime


def test_injection_flushes_dirty_paths_before_compiling() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.sandbox_backend = _FakeBackend(
        files={"agent/feature.py": "def helper(value):\n    return value.strip()\n"}
    )
    runtime.work_dir = "/workspace"
    state = {
        "dirty_paths": {"agent/feature.py"},
        "dirty_unknown": False,
        "focus_paths": ["agent/feature.py"],
        "focus_entities": [],
        "repo_memory_runtime": runtime,
    }

    payload = build_injection_payload(state)

    assert payload is not None
    assert runtime.store.get_file("repo", "agent/feature.py") is not None
    assert runtime.store.get_entity("agent/feature.py:helper") is not None
    assert state["dirty_paths"] == set()
    assert state["dirty_unknown"] is False


def test_injection_flushes_execute_dirty_unknown_via_git_diff() -> None:
    runtime = RepoMemoryRuntime(repo="repo", config=RepoMemoryConfig())
    runtime.sandbox_backend = _FakeBackend(
        files={"agent/feature.py": "def helper(value):\n    return value.strip()\n"},
        status_output="M\tagent/feature.py\n",
    )
    runtime.work_dir = "/workspace"
    state = {
        "dirty_paths": set(),
        "dirty_unknown": True,
        "focus_paths": ["agent/feature.py"],
        "focus_entities": [],
        "repo_memory_runtime": runtime,
    }

    payload = build_injection_payload(state)

    assert payload is not None
    assert runtime.store.get_entity("agent/feature.py:helper") is not None
    assert state["dirty_unknown"] is False


def test_injection_prioritizes_focus_paths_and_bounds_execute_probe_scope() -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        config=RepoMemoryConfig(parse_dirty_path_limit=2),
    )
    runtime.sandbox_backend = _FakeBackend(
        files={
            "agent/focus.py": "def focus_helper(value):\n    return value.strip()\n",
            "agent/first.py": "def first_helper(value):\n    return value.lower()\n",
            "agent/ignored.py": "def ignored_helper(value):\n    return value.upper()\n",
        },
        status_output="M\tagent/first.py\nM\tagent/ignored.py\nM\tagent/focus.py\n",
    )
    runtime.work_dir = "/workspace"
    state = {
        "dirty_paths": set(),
        "dirty_unknown": True,
        "focus_paths": ["agent/focus.py"],
        "focus_entities": [],
        "repo_memory_runtime": runtime,
    }

    payload = build_injection_payload(state)

    assert payload is not None
    assert runtime.store.get_entity("agent/focus.py:focus_helper") is not None
    assert runtime.store.get_entity("agent/first.py:first_helper") is not None
    assert runtime.store.get_entity("agent/ignored.py:ignored_helper") is None


def test_injection_flushes_execute_dirty_unknown_into_postgres_store(
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
        files={"agent/feature.py": "def helper(value):\n    return value.strip()\n"},
        status_output="M\tagent/feature.py\n",
    )
    runtime.work_dir = "/workspace"
    state = {
        "dirty_paths": set(),
        "dirty_unknown": True,
        "focus_paths": ["agent/feature.py"],
        "focus_entities": [],
        "repo_memory_runtime": runtime,
    }

    payload = build_injection_payload(state)

    reloaded = PostgresRepoMemoryStore(
        database_url=postgres_url,
        embedding_provider=postgres_store.embedding_provider,
    )
    assert payload is not None
    assert reloaded.get_entity("agent/feature.py:helper") is not None
    assert reloaded.get_sync_state("repo")["last_compiled_seq"] == 1
    assert state["dirty_unknown"] is False


class _FakeResult:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code


class _FakeBackend:
    def __init__(self, files: dict[str, str], status_output: str = "") -> None:
        self.files = files
        self.status_output = status_output

    def execute(self, command: str) -> _FakeResult:
        if "git diff --name-status --relative" in command:
            return _FakeResult(self.status_output)
        for path, content in self.files.items():
            if f"cat '{path}'" in command or f'cat "{path}"' in command or f"cat {path}" in command:
                return _FakeResult(content)
        return _FakeResult("", exit_code=1)
