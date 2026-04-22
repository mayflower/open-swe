from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import (
    ClaimStatus,
    RepoEvent,
    RepoEventKind,
)
from agent.repo_memory.dreaming import (
    PromotionExplanation,
    explain_dreaming_promotions,
    run_deep_phase,
    run_light_phase,
    run_rem_phase,
    run_repo_memory_dreaming_pass,
)
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime


def _decision(
    event_id: str,
    seq: int,
    *,
    thread: str,
    path: str | None = None,
    entity: str | None = None,
    text: str,
) -> RepoEvent:
    """Repo-scoped decision event.

    Path and entity are optional on purpose: when both are omitted the event is
    repo-scoped, which lets multiple events share a single claim scope while
    still contributing distinct source-thread signals to the diversity gate.
    """
    return RepoEvent(
        repo="repo",
        event_id=event_id,
        kind=RepoEventKind.DECISION,
        summary=text,
        observed_seq=seq,
        path=path,
        entity_id=entity,
        metadata={"thread_id": thread},
    )


def _strict_config(**overrides) -> RepoMemoryConfig:
    base = {
        "embedding_provider": "hashed",
        "embedding_dimensions": 16,
    }
    base.update(overrides)
    return RepoMemoryConfig(**base)


def test_light_phase_stages_candidates_without_promotion_or_snapshot() -> None:
    store = InMemoryRepoMemoryStore()
    config = _strict_config()
    now = datetime(2026, 4, 15, tzinfo=UTC)

    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"t-{i}",
            text="Prefer shared normalization helpers.",
        )
        for i in range(1, 4)
    ]
    for event in events:
        store.append_repo_event(event)

    light = run_light_phase(store, "repo", events, run_id="run-light", now=now, config=config)

    assert light.new_events == 3
    assert light.candidate_claims == 3
    # Three identical-text events sharing a scope should collapse into one
    # claim via pgvector similarity or the OpenClaw-style Jaccard dedup.
    claims = store.list_claims("repo")
    assert len(claims) == 1
    assert light.jaccard_merged + light.merged_claims >= 2
    # No snapshot should exist yet — Light never promotes.
    assert store.get_latest_repo_core_snapshot("repo") is None
    assert all(claim.status == ClaimStatus.CANDIDATE for claim in claims)


def test_rem_phase_boosts_claims_without_touching_status() -> None:
    store = InMemoryRepoMemoryStore()
    config = _strict_config()
    now = datetime(2026, 4, 15, tzinfo=UTC)
    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"t-{i}",
            text="Prefer shared normalization helpers.",
        )
        for i in range(1, 4)
    ]
    for event in events:
        store.append_repo_event(event)
    run_light_phase(store, "repo", events, run_id="run-light", now=now, config=config)

    rem = run_rem_phase(store, "repo", now=now, config=config)

    assert rem.boosted_claim_keys, "REM should record at least one pattern boost"
    claims = store.list_claims("repo")
    assert all(claim.status == ClaimStatus.CANDIDATE for claim in claims)
    phase_boosts = claims[0].metadata.get("phase_boosts", {})
    assert phase_boosts.get("rem", 0) >= 1


def test_deep_phase_is_only_phase_that_writes_snapshot() -> None:
    store = InMemoryRepoMemoryStore()
    config = _strict_config()
    now = datetime(2026, 4, 15, tzinfo=UTC)
    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"t-{i}",
            text="Prefer shared normalization helpers across parser modules.",
        )
        for i in range(1, 6)
    ]
    for event in events:
        store.append_repo_event(event)

    run_light_phase(store, "repo", events, run_id="run-a", now=now, config=config)
    run_rem_phase(store, "repo", now=now, config=config)
    assert store.get_latest_repo_core_snapshot("repo") is None, (
        "Neither Light nor REM may write a snapshot."
    )
    deep = run_deep_phase(
        store, "repo", source_watermark=5, now=now, config=config
    )

    assert deep.scored_claims >= 1
    assert deep.promoted_claims >= 1
    snapshot = store.get_latest_repo_core_snapshot("repo")
    assert snapshot is not None
    assert snapshot.source_watermark == 5


def test_multi_gate_rejects_claims_without_enough_evidence_diversity() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="repo", store=store, config=_strict_config())
    now = datetime(2026, 4, 15, tzinfo=UTC)

    # Only two events, same path, no thread/entity — violates all three gates.
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:1",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=1,
            path="agent/parser.py",
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:2",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=2,
            path="agent/parser.py",
        )
    )

    run = run_repo_memory_dreaming_pass(runtime, worker_id="test", now=now)

    assert run.status == "succeeded"
    assert run.promoted_count == 0
    assert run.snapshot_id is None
    assert store.get_latest_repo_core_snapshot("repo") is None
    claim = store.list_claims("repo")[0]
    assert claim.status != ClaimStatus.PROMOTED
    failures = claim.metadata.get("promotion_gate_failures", [])
    assert any("source_diversity" in reason for reason in failures)


def test_multi_gate_accepts_claims_with_sufficient_diversity() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="repo", store=store, config=_strict_config())
    now = datetime(2026, 4, 15, tzinfo=UTC)

    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"thread-{i}",
            text="Prefer shared normalization helpers across parser modules for robustness.",
        )
        for i in range(1, 7)
    ]
    for event in events:
        store.append_repo_event(event)

    run = run_repo_memory_dreaming_pass(runtime, worker_id="test", now=now)

    assert run.status == "succeeded"
    assert run.promoted_count == 1, run.summary
    snapshot = store.get_latest_repo_core_snapshot("repo")
    assert snapshot is not None
    assert snapshot.source_watermark == 6


def test_max_age_days_gate_drops_stale_candidates() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=store,
        config=_strict_config(dreaming_max_age_days=1.0),
    )
    now = datetime(2026, 4, 15, tzinfo=UTC)
    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"thread-{i}",
            text="Prefer shared normalization helpers across parser modules for robustness.",
        )
        for i in range(1, 7)
    ]
    for event in events:
        store.append_repo_event(event)

    # Stage claims at ``now``, then score 10 days later. They must fail the
    # age gate even though everything else is satisfied.
    run_repo_memory_dreaming_pass(runtime, worker_id="test", now=now)
    store.append_repo_event(
        _decision(
            "decision:trigger",
            7,
            thread="thread-trigger",
            text="Prefer shared normalization helpers across parser modules for robustness.",
        )
    )
    later = now + timedelta(days=10)
    run = run_repo_memory_dreaming_pass(runtime, worker_id="test", now=later)

    assert run.status == "succeeded"
    for claim in store.list_claims("repo"):
        failures = claim.metadata.get("promotion_gate_failures", [])
        if failures:
            assert any("age_days>" in reason for reason in failures)


def test_explain_mode_reports_gate_outcomes_without_mutating_store() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="repo", store=store, config=_strict_config())
    now = datetime(2026, 4, 15, tzinfo=UTC)
    events = [
        _decision(
            f"decision:{i}",
            i,
            thread=f"thread-{i}",
            text="Prefer shared normalization helpers.",
        )
        for i in range(1, 4)
    ]
    for event in events:
        store.append_repo_event(event)
    run_light_phase(store, "repo", events, run_id="run-explain", now=now, config=runtime.config)

    before_status = {claim.claim_key: claim.status for claim in store.list_claims("repo")}
    before_snapshot = store.get_latest_repo_core_snapshot("repo")

    explanations = explain_dreaming_promotions(runtime, now=now)

    # Store must be unchanged — no promotion, no snapshot writes.
    after_status = {claim.claim_key: claim.status for claim in store.list_claims("repo")}
    assert before_status == after_status
    assert store.get_latest_repo_core_snapshot("repo") is before_snapshot
    assert explanations, "explain should yield at least one claim"
    for explanation in explanations:
        assert isinstance(explanation, PromotionExplanation)
        assert "relevance_score" in explanation.score_components
        assert isinstance(explanation.failed_gates, list)
