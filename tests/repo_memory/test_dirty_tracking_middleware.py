from agent.repo_memory.middleware.dirty_tracking import update_state_for_tool
from agent.repo_memory.state import create_repo_memory_state


def test_write_and_edit_mark_paths_dirty() -> None:
    state = create_repo_memory_state()

    update_state_for_tool(state, tool_name="write_file", tool_args={"path": "agent/a.py"})
    update_state_for_tool(state, tool_name="edit_file", tool_args={"path": "agent/b.py"})

    assert state["dirty_paths"] == {"agent/a.py", "agent/b.py"}
    assert state["dirty_unknown"] is False


def test_read_and_grep_enrich_focus_set() -> None:
    state = create_repo_memory_state()

    update_state_for_tool(state, tool_name="read_file", tool_args={"path": "agent/server.py"})
    update_state_for_tool(
        state,
        tool_name="grep",
        tool_args={"pattern": "get_agent"},
        tool_result={"matches": [{"path": "agent/server.py"}, {"path": "agent/prompt.py"}]},
    )

    assert state["focus_paths"] == ["agent/server.py", "agent/prompt.py"]
    assert state["focus_entities"] == ["get_agent"]


def test_successful_execute_marks_dirty_unknown() -> None:
    state = create_repo_memory_state()

    update_state_for_tool(
        state,
        tool_name="execute",
        tool_args={"command": "pytest"},
        tool_result={"exit_code": 0},
    )

    assert state["dirty_unknown"] is True
