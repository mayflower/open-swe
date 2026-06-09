from agent.repo_memory.focus import compute_focus_set


def test_focus_prefers_explicit_signals_before_derived_context() -> None:
    focus = compute_focus_set(
        explicit_paths=["agent/server.py", "agent/server.py"],
        explicit_entities=["get_agent"],
        derived_paths=["agent/server.py", "agent/prompt.py"],
        derived_entities=["get_agent", "construct_system_prompt"],
    )

    assert focus.paths == ["agent/server.py", "agent/prompt.py"]
    assert focus.entities == ["get_agent", "construct_system_prompt"]
