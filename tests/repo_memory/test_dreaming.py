from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.daemon import reembed_repo_memory_repo, run_repo_memory_dreaming_cycle
from agent.repo_memory.domain import (
    ClaimEvidence,
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    MemoryClaim,
    RepoEvent,
    RepoEventKind,
    RevalidationMode,
)
from agent.repo_memory.dreaming import (
    build_candidate_claims_from_events,
    build_snapshot_injection_blocks,
    run_repo_memory_dreaming_pass,
    score_and_transition_claims,
    upsert_candidate_claim,
)
from agent.repo_memory.middleware.injection import build_injection_payload
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime, bind_runtime_context


def test_upsert_candidate_claim_uses_source_identity_over_text() -> None:
    store = InMemoryRepoMemoryStore()
    config = RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16)
    now = datetime(2026, 4, 15, tzinfo=UTC)
    initial_event = RepoEvent(
        repo="repo",
        event_id="repo_event:1",
        kind=RepoEventKind.DECISION,
        summary="Prefer shared normalization helpers.",
        observed_seq=1,
        path="agent/feature.py",
    )
    candidate, evidence = build_candidate_claims_from_events(
        "repo",
        [initial_event],
        now=now,
        config=config,
    )[0]
    stored, merged = upsert_candidate_claim(store, candidate, evidence, config=config)
    store.attach_claim_evidence(stored.claim_key, evidence)

    rerun_event = RepoEvent(
        repo="repo",
        event_id="repo_event:1",
        kind=RepoEventKind.DECISION,
        summary="Prefer shared helper functions for normalization.",
        observed_seq=2,
        path="agent/feature.py",
    )
    rerun_candidate, rerun_evidence = build_candidate_claims_from_events(
        "repo",
        [rerun_event],
        now=now + timedelta(minutes=5),
        config=config,
    )[0]
    updated, rerun_merged = upsert_candidate_claim(
        store,
        rerun_candidate,
        rerun_evidence,
        config=config,
    )

    assert merged is False
    assert rerun_merged is False
    assert len(store.list_claims("repo")) == 1
    assert updated.claim_key == stored.claim_key
    assert updated.text == "Prefer shared helper functions for normalization."
    assert store.get_claim_by_source_identity("repo", "repo_event:1") is not None


def test_related_merge_resolves_second_source_identity_via_claim_evidence() -> None:
    store = InMemoryRepoMemoryStore()
    config = RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16)
    now = datetime(2026, 4, 15, tzinfo=UTC)
    first_candidate, first_evidence = build_candidate_claims_from_events(
        "repo",
        [
            RepoEvent(
                repo="repo",
                event_id="repo_event:1",
                kind=RepoEventKind.DECISION,
                summary="Prefer shared normalization helpers.",
                observed_seq=1,
                path="agent/feature.py",
            )
        ],
        now=now,
        config=config,
    )[0]
    first_claim, _ = upsert_candidate_claim(store, first_candidate, first_evidence, config=config)
    store.attach_claim_evidence(first_claim.claim_key, first_evidence)

    second_candidate, second_evidence = build_candidate_claims_from_events(
        "repo",
        [
            RepoEvent(
                repo="repo",
                event_id="repo_event:2",
                kind=RepoEventKind.DECISION,
                summary="Prefer shared normalization helpers.",
                observed_seq=2,
                path="agent/feature.py",
            )
        ],
        now=now + timedelta(minutes=5),
        config=config,
    )[0]
    merged_claim, merged = upsert_candidate_claim(
        store,
        second_candidate,
        second_evidence,
        config=config,
    )
    second_evidence.claim_key = merged_claim.claim_key
    store.attach_claim_evidence(merged_claim.claim_key, second_evidence)

    resolved = store.get_claim_by_source_identity("repo", "repo_event:2")

    assert merged is True
    assert len(store.list_claims("repo")) == 1
    assert resolved is not None
    assert resolved.claim_key == first_claim.claim_key
    assert "repo_event:2" in resolved.metadata["merged_source_identities"]


def test_score_and_transition_applies_claim_kind_specific_revalidation() -> None:
    store = InMemoryRepoMemoryStore()
    now = datetime(2026, 4, 15, tzinfo=UTC)
    strict_claim = MemoryClaim(
        claim_id="strict",
        claim_key="strict",
        source_identity_key="repo_event:strict",
        repo="repo",
        scope_kind=ClaimScopeKind.PATH,
        scope_ref="agent/missing.py",
        claim_kind=ClaimKind.HIGH_IMPACT_CHANGE,
        text="The parser was reworked heavily.",
        normalized_text="the parser was reworked heavily",
        status=ClaimStatus.CANDIDATE,
        first_seen_at=now,
        last_seen_at=now,
        revalidation_mode=RevalidationMode.STRICT_LIVE_STATE,
    )
    watchout_claim = MemoryClaim(
        claim_id="watchout",
        claim_key="watchout",
        source_identity_key="repo_event:watchout",
        repo="repo",
        scope_kind=ClaimScopeKind.REPO,
        scope_ref="repo",
        claim_kind=ClaimKind.WATCHOUT,
        text="Avoid changing parser output without updating fixture tests.",
        normalized_text="avoid changing parser output without updating fixture tests",
        status=ClaimStatus.CANDIDATE,
        first_seen_at=now,
        last_seen_at=now,
        revalidation_mode=RevalidationMode.EVIDENCE_ONLY,
    )
    store.upsert_claim(strict_claim)
    store.upsert_claim(watchout_claim)
    store.attach_claim_evidence(
        "strict",
        ClaimEvidence(
            evidence_id="evidence:strict",
            repo="repo",
            claim_key="strict",
            run_id="run-1",
            evidence_kind="repo_event",
            evidence_ref="repo_event:strict",
            evidence_text=strict_claim.text,
            weight=0.8,
            observed_at=now,
            source_path="agent/missing.py",
        ),
    )
    store.attach_claim_evidence(
        "watchout",
        ClaimEvidence(
            evidence_id="evidence:watchout",
            repo="repo",
            claim_key="watchout",
            run_id="run-1",
            evidence_kind="repo_event",
            evidence_ref="repo_event:watchout",
            evidence_text=watchout_claim.text,
            weight=1.0,
            observed_at=now,
        ),
    )

    promoted = score_and_transition_claims(
        store,
        "repo",
        now=now + timedelta(hours=1),
        config=RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16),
    )

    rescored = {claim.claim_key: claim for claim in store.list_claims("repo")}
    assert promoted == 0
    assert rescored["strict"].status == ClaimStatus.STALE
    assert rescored["strict"].metadata["last_revalidation_reason"] == "path-missing"
    assert rescored["watchout"].status == ClaimStatus.ACTIVE
    assert rescored["watchout"].metadata["last_revalidation_reason"] == "evidence-present"


def test_dreaming_snapshot_uses_watermark_and_overlay_deduplicates_snapshot_claims() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=store,
        config=RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16),
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:1",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=1,
            path="agent/feature.py",
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:2",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=2,
            path="agent/feature.py",
        )
    )

    run_repo_memory_dreaming_pass(
        runtime,
        worker_id="test-daemon",
        now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
    )

    snapshot = store.get_latest_repo_core_snapshot("repo")
    assert snapshot is not None
    assert snapshot.source_watermark == 2

    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:3",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=3,
            path="agent/feature.py",
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="watchout:4",
            kind=RepoEventKind.WATCHOUT,
            summary="Avoid parser edits without fixture coverage.",
            observed_seq=4,
            path="agent/parser.py",
        )
    )

    blocks = build_snapshot_injection_blocks(
        store,
        "repo",
        config=runtime.config,
        focus_paths=[],
        focus_entities=[],
    )

    assert blocks is not None
    block_map = {block.label: block.value for block in blocks}
    assert "Fresh: Prefer shared normalization helpers." not in block_map["active_design_decisions"]
    assert "Fresh: Avoid parser edits without fixture coverage." in block_map["repo_watchouts"]


def test_run_repo_memory_dreaming_pass_tracks_cursor_snapshot_and_runs() -> None:
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=store,
        config=RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16),
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:1",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=1,
            path="agent/feature.py",
        )
    )
    store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:2",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=2,
            path="agent/feature.py",
        )
    )

    first_run = run_repo_memory_dreaming_pass(
        runtime,
        worker_id="daemon-a",
        now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
    )
    second_run = run_repo_memory_dreaming_pass(
        runtime,
        worker_id="daemon-a",
        now=datetime(2026, 4, 15, 10, 5, tzinfo=UTC),
    )

    assert first_run.status == "succeeded"
    assert first_run.cursor_before == 0
    assert first_run.cursor_after == 2
    assert second_run.signal_count == 0
    assert second_run.cursor_before == 2
    assert second_run.cursor_after == 2
    assert store.get_dreaming_cursor("repo") == 2
    assert store.get_latest_repo_core_snapshot("repo") is not None
    assert [run.status for run in store.list_dream_runs("repo")] == ["succeeded", "succeeded"]
    assert store.acquire_dreaming_lease(
        "repo",
        "daemon-b",
        datetime(2026, 4, 15, 10, 6, tzinfo=UTC),
        30,
    )


def test_build_injection_payload_falls_back_when_dreaming_snapshot_is_missing() -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=InMemoryRepoMemoryStore(),
        config=RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16),
    )
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:1",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=1,
            path="agent/feature.py",
        )
    )
    runtime.store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:2",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=2,
            path="agent/feature.py",
        )
    )

    payload = build_injection_payload(
        {
            "dirty_paths": set(),
            "dirty_unknown": False,
            "focus_paths": [],
            "focus_entities": [],
            "repo_memory_runtime": runtime,
        }
    )

    assert payload is not None
    assert runtime.store.get_latest_repo_core_snapshot("repo") is None
    assert "Prefer shared normalization helpers." in payload["messages"][0]["content"][0]["text"]


def test_bind_runtime_context_does_not_start_in_process_dreaming_daemon() -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=InMemoryRepoMemoryStore(),
        config=RepoMemoryConfig(
            dreaming_daemon_poll_interval_seconds=30,
            embedding_provider="hashed",
            embedding_dimensions=16,
        ),
    )
    bound = bind_runtime_context(runtime, sandbox_backend=object(), work_dir="/workspace")

    assert bound is runtime
    assert runtime.sandbox_backend is not None
    assert runtime.work_dir == "/workspace"


def test_standalone_dreaming_daemon_cycle_discovers_all_repos() -> None:
    store = InMemoryRepoMemoryStore()
    config = RepoMemoryConfig(embedding_provider="hashed", embedding_dimensions=16)
    for repo in ("repo-a", "repo-b"):
        store.append_repo_event(
            RepoEvent(
                repo=repo,
                event_id=f"{repo}:decision:1",
                kind=RepoEventKind.DECISION,
                summary="Prefer shared normalization helpers.",
                observed_seq=1,
                path="agent/feature.py",
            )
        )
        store.append_repo_event(
            RepoEvent(
                repo=repo,
                event_id=f"{repo}:decision:2",
                kind=RepoEventKind.DECISION,
                summary="Prefer shared normalization helpers.",
                observed_seq=2,
                path="agent/feature.py",
            )
        )

    runs = run_repo_memory_dreaming_cycle(
        store,
        config=config,
        now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
        worker_prefix="docker-daemon",
    )

    assert [run.repo for run in runs] == ["repo-a", "repo-b"]
    assert all(run.status == "succeeded" for run in runs)
    assert store.get_dreaming_cursor("repo-a") == 2
    assert store.get_dreaming_cursor("repo-b") == 2
    assert store.get_latest_repo_core_snapshot("repo-a") is not None
    assert store.get_latest_repo_core_snapshot("repo-b") is not None


def test_reembed_repo_memory_repo_refreshes_claim_embeddings() -> None:
    store = InMemoryRepoMemoryStore()
    now = datetime(2026, 4, 15, tzinfo=UTC)
    claim = MemoryClaim(
        claim_id="claim-1",
        claim_key="claim-1",
        source_identity_key="repo_event:1",
        repo="repo",
        scope_kind=ClaimScopeKind.REPO,
        scope_ref="repo",
        claim_kind=ClaimKind.DESIGN_DECISION,
        text="Prefer shared normalization helpers.",
        normalized_text="prefer shared normalization helpers",
        status=ClaimStatus.ACTIVE,
        embedding=[1.0, 0.0, 0.0],
        embedding_provider="legacy",
        embedding_dimensions=3,
        embedding_version="legacy-v1",
        first_seen_at=now,
        last_seen_at=now,
    )
    store.upsert_claim(claim)

    summary = reembed_repo_memory_repo(
        store,
        "repo",
        config=RepoMemoryConfig(
            embedding_provider="hashed",
            embedding_dimensions=16,
            embedding_version="sha256-token-v1",
        ),
    )

    refreshed = store.list_claims("repo")[0]
    assert summary == {"entities": 0, "claims": 1}
    assert refreshed.embedding_provider == "hashed"
    assert refreshed.embedding_dimensions == 16
    assert refreshed.embedding_version == "sha256-token-v1"
    assert len(refreshed.embedding) == 16


def test_postgres_dreaming_pass_persists_claims_snapshot_and_cursor(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    runtime = RepoMemoryRuntime(
        repo="repo",
        store=postgres_store,
        config=RepoMemoryConfig(
            backend="postgres",
            database_url=postgres_url,
            embedding_provider="hashed",
            embedding_dimensions=16,
        ),
    )
    postgres_store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:1",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=1,
            path="agent/feature.py",
        )
    )
    postgres_store.append_repo_event(
        RepoEvent(
            repo="repo",
            event_id="decision:2",
            kind=RepoEventKind.DECISION,
            summary="Prefer shared normalization helpers.",
            observed_seq=2,
            path="agent/feature.py",
        )
    )

    run_repo_memory_dreaming_pass(
        runtime,
        worker_id="postgres-daemon",
        now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
    )

    reloaded = PostgresRepoMemoryStore(
        database_url=postgres_url,
        embedding_provider=postgres_store.embedding_provider,
    )
    claims = reloaded.list_claims("repo")
    snapshot = reloaded.get_latest_repo_core_snapshot("repo")
    runs = reloaded.list_dream_runs("repo")

    assert len(claims) == 1
    assert claims[0].status == ClaimStatus.PROMOTED
    assert snapshot is not None
    assert snapshot.source_watermark == 2
    assert reloaded.get_dreaming_cursor("repo") == 2
    assert runs[-1].status == "succeeded"
