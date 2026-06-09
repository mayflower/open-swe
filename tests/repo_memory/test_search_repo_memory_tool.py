from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import (
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    MemoryClaim,
    RepoEvent,
    RepoEventKind,
    RevalidationMode,
)
from agent.repo_memory.embeddings import build_embedding_provider
from agent.repo_memory.runtime import RepoMemoryRuntime
from agent.tools.search_repo_memory import search_repo_memory


def _build_claim(text: str, provider, now: datetime) -> MemoryClaim:
    embedding = provider.embed(text)
    return MemoryClaim(
        claim_id=f"claim:{text[:12]}",
        claim_key=f"key:{text[:12]}",
        source_identity_key=f"ident:{text[:12]}",
        repo="repo",
        scope_kind=ClaimScopeKind.REPO,
        scope_ref="repo",
        claim_kind=ClaimKind.DESIGN_DECISION,
        text=text,
        normalized_text=text.lower(),
        status=ClaimStatus.ACTIVE,
        first_seen_at=now,
        last_seen_at=now,
        revalidation_mode=RevalidationMode.EVIDENCE_ONLY,
        embedding=embedding,
        embedding_provider=provider.provider_name,
        embedding_dimensions=provider.dimensions,
        embedding_version=provider.version,
    )


def test_search_repo_memory_returns_related_claims_and_events() -> None:
    config = RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16)
    provider = build_embedding_provider(config)
    runtime = RepoMemoryRuntime(repo="repo", config=config)
    now = datetime(2026, 4, 15, tzinfo=UTC)

    runtime.store.upsert_claim(
        _build_claim("Prefer shared normalization helpers in parser code", provider, now)
    )
    runtime.store.upsert_claim(
        _build_claim("Avoid mixing retry backoff strategies inside webhook auth", provider, now)
    )
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="evt:1",
            kind=RepoEventKind.DECISION,
            summary="Shared parser normalization helpers decided",
            observed_seq=1,
            path="agent/parser.py",
        )
    )

    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        payload = search_repo_memory("parser normalization helpers")

    assert payload["repo"] == "repo"
    assert payload["retrieval"] == "in_memory_vector"
    assert payload["claims"], "expected at least one claim hit"
    assert payload["claims"][0]["text"].startswith("Prefer shared normalization")
    assert "vector=" in payload["claims"][0]["explanation"]
    assert payload["events"], "expected at least one event hit"
    assert "lexical_only" in payload["events"][0]["explanation"]


def test_search_repo_memory_reports_unavailable_when_runtime_missing() -> None:
    with patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {}},
    ):
        payload = search_repo_memory("anything")

    assert payload["retrieval"] == "unavailable"
    assert payload["claims"] == []
    assert payload["events"] == []
