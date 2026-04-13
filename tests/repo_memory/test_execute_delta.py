from agent.repo_memory.delta import mark_execute_dirty_unknown, parse_name_status_diff


def test_execute_marks_dirty_unknown_on_success() -> None:
    state = {"dirty_unknown": False}
    mark_execute_dirty_unknown(state, 0)
    assert state["dirty_unknown"] is True


def test_name_status_diff_prefers_changed_paths() -> None:
    diff = "M\tagent/server.py\nA\tagent/tools/search_similar_code.py\n"
    assert parse_name_status_diff(diff) == [
        "agent/server.py",
        "agent/tools/search_similar_code.py",
    ]
