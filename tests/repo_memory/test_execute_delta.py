from unittest.mock import patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.delta import mark_execute_dirty_unknown, parse_name_status_diff
from agent.repo_memory.middleware.dirty_tracking import update_state_for_tool
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.repo_memory.state import create_repo_memory_state


def test_execute_marks_dirty_unknown_on_success() -> None:
    state = {"dirty_unknown": False}
    mark_execute_dirty_unknown(state, 0)
    assert state["dirty_unknown"] is True


def test_execute_marks_dirty_unknown_for_configured_exit_codes() -> None:
    state = create_repo_memory_state()
    runtime = RepoMemoryRuntime(
        repo="repo",
        config=RepoMemoryConfig(dirty_execute_exit_codes={1, 2}),
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        update_state_for_tool(
            state,
            tool_name="execute",
            tool_args={"command": "pytest"},
            tool_result={"exit_code": 1},
        )

    assert state["dirty_unknown"] is True


def test_execute_ignores_exit_codes_outside_config() -> None:
    state = create_repo_memory_state()
    runtime = RepoMemoryRuntime(
        repo="repo",
        config=RepoMemoryConfig(dirty_execute_exit_codes={1}),
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        update_state_for_tool(
            state,
            tool_name="execute",
            tool_args={"command": "pytest"},
            tool_result={"exit_code": 0},
        )

    assert state["dirty_unknown"] is False


def test_name_status_diff_prefers_changed_paths() -> None:
    diff = "M\tagent/server.py\nA\tagent/tools/search_similar_code.py\n"
    assert parse_name_status_diff(diff) == [
        "agent/server.py",
        "agent/tools/search_similar_code.py",
    ]


def test_name_status_diff_uses_destination_path_for_renames() -> None:
    diff = "R100\tagent/old_name.py\tagent/new_name.py\n"
    assert parse_name_status_diff(diff) == ["agent/new_name.py"]
