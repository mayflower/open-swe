from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolved_embedding_provider(provider: str | None) -> str:
    return provider or os.getenv("REPO_MEMORY_EMBEDDING_PROVIDER", "openai")


@dataclass(slots=True)
class RepoMemoryConfig:
    """Configuration for repository memory behavior."""

    backend: str = field(default_factory=lambda: os.getenv("REPO_MEMORY_BACKEND", "auto"))
    database_url: str | None = field(default_factory=lambda: os.getenv("REPO_MEMORY_DATABASE_URL"))
    embedding_provider: str | None = None
    embedding_dimensions: int | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
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
    dreaming_merge_similarity_threshold: float = 0.82
    dreaming_related_similarity_threshold: float = 0.6
    dreaming_overlay_similarity_threshold: float = 0.8
    dreaming_overlay_max_items: int = 4
    dreaming_daemon_poll_interval_seconds: int = 30
    dreaming_daemon_lease_ttl_seconds: int = 60
    # OpenClaw-style multi-gate promotion knobs. A claim is only promoted when
    # all three gates pass. Defaults mirror OpenClaw's Deep phase thresholds.
    dreaming_promotion_min_score: float = 0.8
    dreaming_promotion_min_evidence_count: int = 3
    dreaming_promotion_min_source_diversity: int = 3
    # Recency decay. The recency score halves every recency_half_life_days and
    # candidates older than max_age_days are dropped from promotion.
    dreaming_recency_half_life_days: float = 3.0
    dreaming_max_age_days: float = 30.0
    # Phase boosts. Light contributes at most light_phase_boost_cap points to
    # the composite score; REM contributes at most rem_phase_boost_cap.
    dreaming_light_phase_boost_cap: float = 0.05
    dreaming_rem_phase_boost_cap: float = 0.08
    # Light phase token-set dedup threshold (Jaccard). A candidate whose
    # normalized text overlaps an existing claim above this threshold is
    # treated as a reinforcement rather than a new claim.
    dreaming_jaccard_dedup_threshold: float = 0.9

    def __post_init__(self) -> None:
        self.embedding_provider = _resolved_embedding_provider(self.embedding_provider)
        self.embedding_model = self.embedding_model or os.getenv(
            "REPO_MEMORY_EMBEDDING_MODEL",
            "text-embedding-3-small",
        )
        provider = self.embedding_provider.lower()
        if self.embedding_dimensions is None:
            default_dimensions = 16 if provider in {"hashed", "hash", "local"} else 1536
            self.embedding_dimensions = _env_int(
                "REPO_MEMORY_EMBEDDING_DIMENSIONS",
                default_dimensions,
            )
        if self.embedding_version is None:
            configured_version = os.getenv("REPO_MEMORY_EMBEDDING_VERSION")
            if configured_version:
                self.embedding_version = configured_version
            elif provider in {"hashed", "hash", "local"}:
                self.embedding_version = "sha256-token-v1"
            else:
                self.embedding_version = f"{self.embedding_model}:{self.embedding_dimensions}"

    def resolved_backend(self) -> str:
        if self.backend != "auto":
            return self.backend
        if self.database_url:
            return "postgres"
        if _env_bool("REPO_MEMORY_ALLOW_IN_MEMORY", False):
            return "memory"
        return "unconfigured"
