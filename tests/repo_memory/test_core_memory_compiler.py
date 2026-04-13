from agent.repo_memory.compiler import compile_core_memory_blocks
from agent.repo_memory.domain import RepoEvent, RepoEventKind


def test_compiler_emits_expected_block_labels() -> None:
    events = [
        RepoEvent(
            repo="repo",
            event_id="1",
            kind=RepoEventKind.DECISION,
            summary="Keep memory separate from exact file outputs.",
            observed_seq=1,
            path="agent/server.py",
        ),
        RepoEvent(
            repo="repo",
            event_id="2",
            kind=RepoEventKind.WATCHOUT,
            summary="execute can mutate files indirectly.",
            observed_seq=2,
        ),
    ]

    blocks = compile_core_memory_blocks(
        "repo",
        events,
        {
            "repo_rules": 40,
            "active_design_decisions": 40,
            "recent_high_impact_changes": 40,
            "repo_watchouts": 40,
        },
        focus_paths=["agent/server.py"],
    )

    assert [block.label for block in blocks] == [
        "repo_rules",
        "active_design_decisions",
        "recent_high_impact_changes",
        "repo_watchouts",
    ]
    assert "exact file outputs" in blocks[0].value
