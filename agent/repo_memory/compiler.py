from __future__ import annotations

from .domain import RepoCoreBlock, RepoEventKind


def _trim_words(text: str, budget: int) -> str:
    words = text.split()
    return " ".join(words[:budget])


def compile_core_memory_blocks(
    repo: str,
    events: list,
    token_budgets: dict[str, int],
    focus_paths: list[str] | None = None,
    focus_entities: list[str] | None = None,
) -> list[RepoCoreBlock]:
    focus_paths = focus_paths or []
    focus_entities = focus_entities or []
    decisions = [event for event in events if event.kind == RepoEventKind.DECISION]
    watchouts = [event for event in events if event.kind == RepoEventKind.WATCHOUT]
    edits = [event for event in events if event.kind == RepoEventKind.EDIT]
    focus_events = [
        event
        for event in events
        if (event.path and event.path in focus_paths)
        or (event.entity_id and event.entity_id in focus_entities)
    ]
    blocks = [
        RepoCoreBlock(
            label="repo_rules",
            description=f"Rules observed for {repo}",
            value=_trim_words(
                " ".join(event.summary for event in decisions[:2]) or "No repo rules captured yet.",
                token_budgets["repo_rules"],
            ),
            token_budget=token_budgets["repo_rules"],
        ),
        RepoCoreBlock(
            label="active_design_decisions",
            description="Current design decisions",
            value=_trim_words(
                " ".join(event.summary for event in decisions[:4])
                or "No active design decisions recorded.",
                token_budgets["active_design_decisions"],
            ),
            token_budget=token_budgets["active_design_decisions"],
        ),
        RepoCoreBlock(
            label="recent_high_impact_changes",
            description="Recent important changes",
            value=_trim_words(
                " ".join(event.summary for event in (focus_events or edits)[:4])
                or "No high impact changes recorded.",
                token_budgets["recent_high_impact_changes"],
            ),
            token_budget=token_budgets["recent_high_impact_changes"],
        ),
        RepoCoreBlock(
            label="repo_watchouts",
            description="Known hazards and watchouts",
            value=_trim_words(
                " ".join(event.summary for event in watchouts[:4]) or "No watchouts recorded.",
                token_budgets["repo_watchouts"],
            ),
            token_budget=token_budgets["repo_watchouts"],
        ),
    ]
    return blocks


def render_repo_memory_message(blocks: list[RepoCoreBlock]) -> str:
    lines = ["Repository memory"]
    for block in blocks:
        lines.append(f"[{block.label}] {block.description}")
        lines.append(block.value)
    return "\n".join(lines)
