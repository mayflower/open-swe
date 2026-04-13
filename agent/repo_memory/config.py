from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RepoMemoryConfig:
    """Configuration for repository memory behavior."""

    repo_scope_only: bool = True
    max_core_memory_tokens: int = 600
    core_block_token_budgets: dict[str, int] = field(
        default_factory=lambda: {
            "repo_rules": 120,
            "active_design_decisions": 180,
            "recent_high_impact_changes": 180,
            "repo_watchouts": 120,
        }
    )
    max_event_search_results: int = 5
    max_similarity_results: int = 5
    focus_path_limit: int = 8
    parse_dirty_path_limit: int = 24
    dirty_execute_exit_codes: set[int] = field(default_factory=lambda: {0})
    same_language_bonus: float = 2.0
    same_kind_bonus: float = 1.0
    freshness_bonus: float = 0.5

