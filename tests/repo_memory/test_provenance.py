from agent.repo_memory.provenance.git_history import (
    BlameRecord,
    aggregate_blame,
    maybe_load_deep_history,
)


def test_blame_records_aggregate_last_touch_signals() -> None:
    provenance = aggregate_blame(
        [
            BlameRecord(commit="c3", author="alice", summary="refined helper"),
            BlameRecord(commit="c2", author="alice", summary="introduced helper"),
            BlameRecord(commit="c1", author="bob", summary="moved helper"),
        ]
    )

    assert provenance["last_commit"] == "c3"
    assert provenance["top_authors"][0] == ("alice", 2)


def test_deep_history_is_lazy() -> None:
    calls: list[str] = []
    result = maybe_load_deep_history(False, lambda: calls.append("loaded"))
    assert result == []
    assert calls == []
