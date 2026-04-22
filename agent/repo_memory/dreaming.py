from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import RepoMemoryConfig
from .domain import (
    ClaimEvidence,
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    DreamRun,
    MemoryClaim,
    RepoCoreBlock,
    RepoCoreSnapshot,
    RepoEvent,
    RepoEventKind,
    RevalidationMode,
)
from .embeddings import build_embedding_provider
from .runtime import runtime_attr

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_SNAPSHOT_BLOCKS: dict[ClaimKind, str] = {
    ClaimKind.DESIGN_DECISION: "active_design_decisions",
    ClaimKind.WATCHOUT: "repo_watchouts",
    ClaimKind.HIGH_IMPACT_CHANGE: "recent_high_impact_changes",
}
_BLOCK_DESCRIPTIONS = {
    "active_design_decisions": "Current design decisions",
    "repo_watchouts": "Known hazards and watchouts",
    "recent_high_impact_changes": "Recent important changes",
}
_BLOCK_LIMITS = {
    "active_design_decisions": 3,
    "repo_watchouts": 3,
    "recent_high_impact_changes": 4,
}

_PHASE_BOOST_METADATA_KEY = "phase_boosts"


@dataclass(slots=True)
class LightPhaseResult:
    """Result of the Light phase.

    Light ingests new repo events, dedupes via pgvector + Jaccard, and upserts
    candidate claims with attached evidence. It never promotes and never writes
    a snapshot. The returned counts are recorded in the DreamRun summary.
    """

    new_events: int = 0
    candidate_claims: int = 0
    merged_claims: int = 0
    jaccard_merged: int = 0
    touched_claim_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RemPhaseResult:
    """Result of the REM phase.

    REM inspects recent claim activity and records a small, capped boost on
    claims that show repeating themes or cross-source consolidation. It does
    not mutate claim status.
    """

    boosted_claim_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DeepPhaseResult:
    """Result of the Deep phase.

    Deep scores every active/candidate claim, applies the multi-gate promotion
    check, transitions status, and compiles a snapshot when at least one claim
    was promoted. Deep is the only phase permitted to write to
    ``repo_core_snapshots``.
    """

    scored_claims: int = 0
    promoted_claims: int = 0
    snapshot: RepoCoreSnapshot | None = None


@dataclass(slots=True)
class PromotionExplanation:
    """Per-claim explain payload used by the dry-run preview."""

    claim_key: str
    claim_kind: str
    status: str
    score: float
    would_promote: bool
    failed_gates: list[str]
    score_components: dict[str, float | str]


def supports_dreaming(store: object) -> bool:
    return all(
        hasattr(store, method)
        for method in (
            "upsert_claim",
            "get_claim_by_source_identity",
            "list_claims",
            "attach_claim_evidence",
            "find_related_claims",
            "create_repo_core_snapshot",
            "get_latest_repo_core_snapshot",
            "create_dream_run",
            "finalize_dream_run",
            "acquire_dreaming_lease",
            "release_dreaming_lease",
            "get_dreaming_cursor",
            "set_dreaming_cursor",
        )
    )


def build_candidate_claims_from_events(
    repo: str,
    events: list[RepoEvent],
    *,
    now: datetime,
    config: RepoMemoryConfig,
) -> list[tuple[MemoryClaim, ClaimEvidence]]:
    provider = build_embedding_provider(config)
    candidates: list[tuple[MemoryClaim, ClaimEvidence]] = []
    for event in events:
        claim_kind = _claim_kind_for_event(event)
        if claim_kind is None:
            continue
        scope_kind, scope_ref = _scope_for_event(repo, event)
        source_identity_key = event.event_id
        normalized_text = normalize_claim_text(event.summary)
        claim_key = _claim_key(
            repo=repo,
            source_identity_key=source_identity_key,
            claim_kind=claim_kind,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            normalized_text=normalized_text,
        )
        text = event.summary.strip()
        embedding = provider.embed(text)
        claim = MemoryClaim(
            claim_id=claim_key,
            claim_key=claim_key,
            source_identity_key=source_identity_key,
            repo=repo,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            claim_kind=claim_kind,
            text=text,
            normalized_text=normalized_text,
            status=ClaimStatus.CANDIDATE,
            first_seen_at=now,
            last_seen_at=now,
            revalidation_mode=revalidation_mode_for_claim_kind(claim_kind),
            embedding=embedding,
            embedding_provider=provider.provider_name,
            embedding_dimensions=provider.dimensions,
            embedding_version=provider.version,
            metadata={
                "source_event_id": event.event_id,
                "observed_seq": event.observed_seq,
                "path": event.path,
                "entity_id": event.entity_id,
                "contradicts_claim_key": event.metadata.get("contradicts_claim_key"),
            },
        )
        evidence = ClaimEvidence(
            evidence_id=f"repo_event:{event.event_id}",
            repo=repo,
            claim_key=claim_key,
            run_id=None,
            evidence_kind="repo_event",
            evidence_ref=event.event_id,
            evidence_text=event.summary,
            weight=_event_importance(event),
            observed_at=now,
            source_thread_id=event.metadata.get("thread_id"),
            source_path=event.path,
            source_entity_id=event.entity_id,
            metadata={"observed_seq": event.observed_seq, **event.metadata},
        )
        candidates.append((claim, evidence))
    return candidates


def run_light_phase(
    store: object,
    repo: str,
    events: list[RepoEvent],
    *,
    run_id: str,
    now: datetime,
    config: RepoMemoryConfig,
) -> LightPhaseResult:
    """Ingest events into candidate claims with pgvector + Jaccard dedup.

    Light stages reinforcement signals. It does not score or promote.
    """
    result = LightPhaseResult()
    result.new_events = len(events)
    candidates = build_candidate_claims_from_events(repo, events, now=now, config=config)
    result.candidate_claims = len(candidates)

    for candidate, evidence in candidates:
        claim, vector_merged, jaccard_merged = _upsert_candidate_with_dedup(
            store, candidate, evidence, config=config
        )
        evidence.claim_key = claim.claim_key
        evidence.run_id = run_id
        store.attach_claim_evidence(claim.claim_key, evidence)
        _record_phase_hit(store, claim, phase="light")
        result.touched_claim_keys.append(claim.claim_key)
        if vector_merged:
            result.merged_claims += 1
        if jaccard_merged:
            result.jaccard_merged += 1
    return result


def run_rem_phase(
    store: object,
    repo: str,
    *,
    now: datetime,
    config: RepoMemoryConfig,
    touched_claim_keys: list[str] | None = None,
) -> RemPhaseResult:
    """Record REM pattern signals on active claims.

    REM strengthens claims that show cross-source consolidation or concept-tag
    recurrence. It never mutates ``status``.
    """
    result = RemPhaseResult()
    touched = set(touched_claim_keys or [])
    for claim in store.list_claims(repo):
        if touched and claim.claim_key not in touched:
            continue
        evidence = list(store.list_claim_evidence(repo, claim.claim_key))
        if not _has_rem_signal(claim, evidence):
            continue
        _record_phase_hit(store, claim, phase="rem")
        result.boosted_claim_keys.append(claim.claim_key)
    return result


def run_deep_phase(
    store: object,
    repo: str,
    *,
    source_watermark: int,
    now: datetime,
    config: RepoMemoryConfig,
) -> DeepPhaseResult:
    """Score every claim, apply the promotion gates, and compile a snapshot.

    Deep is the only phase allowed to write to ``repo_core_snapshots``. The
    returned snapshot is ``None`` when no claim cleared the gates.
    """
    result = DeepPhaseResult()
    claims_before = store.list_claims(repo)
    result.scored_claims = len(claims_before)
    result.promoted_claims = score_and_transition_claims(
        store, repo, now=now, config=config
    )
    snapshot = compile_repo_core_snapshot(
        store,
        repo,
        source_watermark=source_watermark,
        now=now,
        config=config,
    )
    if snapshot is not None:
        store.create_repo_core_snapshot(snapshot)
        result.snapshot = snapshot
    return result


def run_repo_memory_dreaming_pass(
    runtime: object,
    *,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> DreamRun:
    repo = runtime_attr(runtime, "repo")
    store = runtime_attr(runtime, "store")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not repo or store is None or not supports_dreaming(store):
        raise ValueError("Repo-memory runtime does not support Dreaming")

    run_now = now or datetime.now(UTC)
    worker = worker_id or f"dreaming-daemon:{repo}"
    run = DreamRun(
        run_id=f"dream-run:{uuid.uuid4().hex}",
        repo=repo,
        run_kind="daemon",
        status="started",
        started_at=run_now,
        worker_id=worker,
    )

    acquired = store.acquire_dreaming_lease(
        repo,
        worker,
        run_now,
        config.dreaming_daemon_lease_ttl_seconds,
    )
    if not acquired:
        run.status = "skipped"
        run.finished_at = run_now
        run.summary = {"reason": "lease-held"}
        store.create_dream_run(run)
        store.finalize_dream_run(run)
        return run

    try:
        cursor_before = store.get_dreaming_cursor(repo)
        watermark = store.get_sync_state(repo).get("last_observed_seq", 0)
        run.cursor_before = cursor_before
        store.create_dream_run(run)

        new_events = [
            event
            for event in store.list_repo_events(repo)
            if event.observed_seq > cursor_before
        ]

        light = run_light_phase(
            store, repo, new_events, run_id=run.run_id, now=run_now, config=config
        )
        rem = run_rem_phase(
            store,
            repo,
            now=run_now,
            config=config,
            touched_claim_keys=light.touched_claim_keys,
        )
        deep = run_deep_phase(
            store, repo, source_watermark=watermark, now=run_now, config=config
        )

        run.signal_count = light.new_events
        run.claim_candidate_count = light.candidate_claims
        run.merged_count = light.merged_claims + light.jaccard_merged
        run.promoted_count = deep.promoted_claims
        if deep.snapshot is not None:
            run.snapshot_id = deep.snapshot.snapshot_id

        store.set_dreaming_cursor(repo, watermark)
        run.cursor_after = watermark
        run.status = "succeeded"
        run.finished_at = run_now
        run.summary = {
            "signal_count": run.signal_count,
            "claim_candidate_count": run.claim_candidate_count,
            "merged_count": run.merged_count,
            "jaccard_merged_count": light.jaccard_merged,
            "promoted_count": run.promoted_count,
            "rem_boosted_count": len(rem.boosted_claim_keys),
            "scored_count": deep.scored_claims,
            "snapshot_id": run.snapshot_id,
        }
        store.finalize_dream_run(run)
        return run
    except Exception as exc:
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.summary = {"error": str(exc)}
        store.finalize_dream_run(run)
        raise
    finally:
        store.release_dreaming_lease(repo, worker)


def run_repo_memory_dreaming_loop(
    runtime: object,
    *,
    worker_id: str | None = None,
    iterations: int | None = None,
    sleep_seconds: int | None = None,
    sleep_fn: Any = time.sleep,
) -> list[DreamRun]:
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    runs: list[DreamRun] = []
    target_iterations = iterations if iterations is not None else 1
    pause = sleep_seconds if sleep_seconds is not None else config.dreaming_daemon_poll_interval_seconds
    for index in range(target_iterations):
        runs.append(run_repo_memory_dreaming_pass(runtime, worker_id=worker_id))
        if index + 1 < target_iterations:
            sleep_fn(pause)
    return runs


def explain_dreaming_promotions(
    runtime: object,
    *,
    now: datetime | None = None,
) -> list[PromotionExplanation]:
    """Dry-run the Deep phase scoring and report gate outcomes per claim.

    The store is not mutated — nothing is promoted, no snapshot is written.
    """
    repo = runtime_attr(runtime, "repo")
    store = runtime_attr(runtime, "store")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not repo or store is None or not supports_dreaming(store):
        raise ValueError("Repo-memory runtime does not support Dreaming")

    run_now = now or datetime.now(UTC)
    claims = store.list_claims(repo)
    claims_by_key = {claim.claim_key: claim for claim in claims}
    explanations: list[PromotionExplanation] = []
    for claim in claims:
        evidence = list(store.list_claim_evidence(repo, claim.claim_key))
        components, revalidation_passed = _score_components(
            store,
            claim,
            evidence,
            claims_by_key=claims_by_key,
            now=run_now,
            config=config,
        )
        score = _combine_score(components)
        gate_failures = _evaluate_promotion_gates(
            claim,
            evidence,
            score=score,
            components=components,
            revalidation_passed=revalidation_passed,
            config=config,
            now=run_now,
        )
        explanations.append(
            PromotionExplanation(
                claim_key=claim.claim_key,
                claim_kind=claim.claim_kind.value,
                status=claim.status.value,
                score=score,
                would_promote=not gate_failures,
                failed_gates=gate_failures,
                score_components=components,
            )
        )
    return explanations


def build_snapshot_injection_blocks(
    store: object,
    repo: str,
    *,
    config: RepoMemoryConfig,
    focus_paths: list[str] | None = None,
    focus_entities: list[str] | None = None,
) -> list[RepoCoreBlock] | None:
    if not supports_dreaming(store):
        return None
    snapshot = store.get_latest_repo_core_snapshot(repo)
    if snapshot is None:
        return None

    claims_by_key = {claim.claim_key: claim for claim in store.list_claims(repo)}
    blocks = [
        RepoCoreBlock(
            label=block.label,
            description=block.description,
            value=block.value,
            token_budget=block.token_budget,
            read_only=block.read_only,
        )
        for block in snapshot.blocks
    ]
    overlay_claims = _build_overlay_claims(
        store,
        repo,
        snapshot=snapshot,
        config=config,
        focus_paths=focus_paths or [],
        focus_entities=focus_entities or [],
        claims_by_key=claims_by_key,
    )
    if overlay_claims:
        blocks = _apply_overlay_to_blocks(blocks, overlay_claims)
    for block in blocks:
        store.set_core_block(repo, block)
    return blocks


def upsert_candidate_claim(
    store: object,
    candidate: MemoryClaim,
    evidence: ClaimEvidence,
    *,
    config: RepoMemoryConfig,
) -> tuple[MemoryClaim, bool]:
    """Back-compat wrapper. Prefer :func:`run_light_phase` which also applies
    Jaccard dedup and records phase hits.
    """
    claim, vector_merged, jaccard_merged = _upsert_candidate_with_dedup(
        store, candidate, evidence, config=config
    )
    return claim, vector_merged or jaccard_merged


def score_and_transition_claims(
    store: object,
    repo: str,
    *,
    now: datetime,
    config: RepoMemoryConfig,
) -> int:
    """Score and transition every claim for ``repo`` and return promoted count.

    The return type stays an int for backwards compatibility with callers
    outside the Dreaming pipeline. The Deep phase derives the scored count
    itself when it needs to report on the full run.
    """
    claims = store.list_claims(repo)
    if not claims:
        return 0
    claims_by_key = {claim.claim_key: claim for claim in claims}
    promoted_count = 0
    for claim in claims:
        evidence = list(store.list_claim_evidence(repo, claim.claim_key))
        components, revalidation_passed = _score_components(
            store,
            claim,
            evidence,
            claims_by_key=claims_by_key,
            now=now,
            config=config,
        )
        score = _combine_score(components)
        gate_failures = _evaluate_promotion_gates(
            claim,
            evidence,
            score=score,
            components=components,
            revalidation_passed=revalidation_passed,
            config=config,
            now=now,
        )
        status = _resolve_status(
            claim=claim,
            score=score,
            components=components,
            revalidation_passed=revalidation_passed,
            gate_failures=gate_failures,
            config=config,
        )
        updated = replace(
            claim,
            score=score,
            score_components=components,
            status=status,
            last_revalidated_at=now,
            metadata={
                **claim.metadata,
                "applied_revalidation_mode": claim.revalidation_mode.value,
                "last_revalidation_reason": components["revalidation_reason"],
                "promotion_gate_failures": gate_failures,
            },
        )
        store.upsert_claim(updated)
        claims_by_key[updated.claim_key] = updated
        if updated.status == ClaimStatus.PROMOTED:
            promoted_count += 1
    return promoted_count


def compile_repo_core_snapshot(
    store: object,
    repo: str,
    *,
    source_watermark: int,
    now: datetime,
    config: RepoMemoryConfig,
) -> RepoCoreSnapshot | None:
    claims = [
        claim
        for claim in store.list_claims(repo, statuses={ClaimStatus.PROMOTED})
        if claim.claim_kind in _SNAPSHOT_BLOCKS
    ]
    if not claims:
        return None
    grouped: dict[str, list[MemoryClaim]] = {}
    for claim in claims:
        grouped.setdefault(_SNAPSHOT_BLOCKS[claim.claim_kind], []).append(claim)

    blocks: list[RepoCoreBlock] = []
    source_claim_keys: list[str] = []
    for label, claim_group in grouped.items():
        limited = claim_group[: _BLOCK_LIMITS[label]]
        source_claim_keys.extend(claim.claim_key for claim in limited)
        value = _trim_lines(
            [claim.text for claim in limited],
            config.core_block_token_budgets.get(label, 120),
        )
        blocks.append(
            RepoCoreBlock(
                label=label,
                description=_BLOCK_DESCRIPTIONS[label],
                value=value,
                token_budget=config.core_block_token_budgets.get(label, 120),
            )
        )
    blocks.sort(key=lambda block: block.label)
    return RepoCoreSnapshot(
        snapshot_id=f"{repo}:snapshot:{source_watermark}",
        repo=repo,
        compiled_at=now,
        source_watermark=source_watermark,
        blocks=blocks,
        source_claim_keys=source_claim_keys,
        metadata={"claim_count": len(source_claim_keys)},
    )


def normalize_claim_text(text: str) -> str:
    return " ".join(token.lower() for token in _TOKEN_RE.findall(text))


def revalidation_mode_for_claim_kind(claim_kind: ClaimKind) -> RevalidationMode:
    if claim_kind == ClaimKind.HIGH_IMPACT_CHANGE:
        return RevalidationMode.STRICT_LIVE_STATE
    if claim_kind == ClaimKind.REUSE_HINT:
        return RevalidationMode.STRICT_LIVE_STATE
    if claim_kind == ClaimKind.WATCHOUT:
        return RevalidationMode.EVIDENCE_ONLY
    return RevalidationMode.EVIDENCE_ONLY


def _claim_kind_for_event(event: RepoEvent) -> ClaimKind | None:
    if event.kind == RepoEventKind.DECISION:
        return ClaimKind.DESIGN_DECISION
    if event.kind == RepoEventKind.WATCHOUT:
        return ClaimKind.WATCHOUT
    if event.kind in {RepoEventKind.EDIT, RepoEventKind.OBSERVATION}:
        return ClaimKind.HIGH_IMPACT_CHANGE
    return None


def _scope_for_event(repo: str, event: RepoEvent) -> tuple[ClaimScopeKind, str]:
    if event.entity_id:
        return ClaimScopeKind.ENTITY, event.entity_id
    if event.path:
        return ClaimScopeKind.PATH, event.path
    return ClaimScopeKind.REPO, repo


def _claim_key(
    *,
    repo: str,
    source_identity_key: str,
    claim_kind: ClaimKind,
    scope_kind: ClaimScopeKind,
    scope_ref: str,
    normalized_text: str,
) -> str:
    stable_part = source_identity_key or normalized_text
    return f"{repo}:{claim_kind.value}:{scope_kind.value}:{scope_ref}:{stable_part}"


def _merge_claim(existing: MemoryClaim, candidate: MemoryClaim) -> MemoryClaim:
    return replace(
        existing,
        text=candidate.text,
        normalized_text=candidate.normalized_text,
        last_seen_at=candidate.last_seen_at,
        embedding=candidate.embedding,
        metadata={**existing.metadata, **candidate.metadata},
    )


def _upsert_candidate_with_dedup(
    store: object,
    candidate: MemoryClaim,
    evidence: ClaimEvidence,
    *,
    config: RepoMemoryConfig,
) -> tuple[MemoryClaim, bool, bool]:
    existing = store.get_claim_by_source_identity(candidate.repo, candidate.source_identity_key)
    if existing is not None:
        updated = _merge_claim(existing, candidate)
        return store.upsert_claim(updated), False, False

    related = store.find_related_claims(
        candidate.repo,
        candidate.embedding,
        claim_kind=candidate.claim_kind,
        scope_kind=candidate.scope_kind,
        scope_ref=candidate.scope_ref,
        limit=3,
    )
    if related and related[0][1] >= config.dreaming_merge_similarity_threshold:
        target, _similarity = related[0]
        merged = _merge_claim(target, candidate)
        merged_sources = list(merged.metadata.get("merged_source_identities", []))
        if candidate.source_identity_key not in merged_sources:
            merged_sources.append(candidate.source_identity_key)
        merged.metadata["merged_source_identities"] = merged_sources
        return store.upsert_claim(merged), True, False

    jaccard_match = _find_jaccard_match(store, candidate, config)
    if jaccard_match is not None:
        merged = _merge_claim(jaccard_match, candidate)
        merged_sources = list(merged.metadata.get("merged_source_identities", []))
        if candidate.source_identity_key not in merged_sources:
            merged_sources.append(candidate.source_identity_key)
        merged.metadata["merged_source_identities"] = merged_sources
        return store.upsert_claim(merged), False, True

    return store.upsert_claim(candidate), False, False


def _find_jaccard_match(
    store: object, candidate: MemoryClaim, config: RepoMemoryConfig
) -> MemoryClaim | None:
    threshold = config.dreaming_jaccard_dedup_threshold
    candidate_tokens = _token_set(candidate.normalized_text)
    if not candidate_tokens:
        return None
    best: tuple[MemoryClaim, float] | None = None
    for claim in store.list_claims(candidate.repo):
        if claim.claim_key == candidate.claim_key:
            continue
        if claim.claim_kind != candidate.claim_kind:
            continue
        if claim.scope_kind != candidate.scope_kind or claim.scope_ref != candidate.scope_ref:
            continue
        similarity = _jaccard_similarity(candidate_tokens, _token_set(claim.normalized_text))
        if similarity >= threshold and (best is None or similarity > best[1]):
            best = (claim, similarity)
    return best[0] if best is not None else None


def _token_set(text: str) -> set[str]:
    return {token for token in text.split() if token}


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left & right
    union = left | right
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _has_rem_signal(claim: MemoryClaim, evidence: list[ClaimEvidence]) -> bool:
    distinct_runs = {item.run_id for item in evidence if item.run_id}
    distinct_threads = {item.source_thread_id for item in evidence if item.source_thread_id}
    distinct_paths = {item.source_path for item in evidence if item.source_path}
    return (
        len(distinct_runs) >= 2
        or len(distinct_threads) >= 2
        or len(distinct_paths) >= 2
    )


def _record_phase_hit(store: object, claim: MemoryClaim, *, phase: str) -> None:
    boosts: dict[str, int] = dict(claim.metadata.get(_PHASE_BOOST_METADATA_KEY, {}))
    boosts[phase] = int(boosts.get(phase, 0)) + 1
    updated_metadata = {**claim.metadata, _PHASE_BOOST_METADATA_KEY: boosts}
    updated = replace(claim, metadata=updated_metadata)
    store.upsert_claim(updated)


def _score_components(
    store: object,
    claim: MemoryClaim,
    evidence: list[ClaimEvidence],
    *,
    claims_by_key: dict[str, MemoryClaim],
    now: datetime,
    config: RepoMemoryConfig,
) -> tuple[dict[str, float | str], bool]:
    relevance_score = _relevance_score(evidence)
    frequency_score = _frequency_score(evidence)
    query_diversity_score = _query_diversity_score(evidence)
    recency_score = _recency_score(
        now,
        claim.last_seen_at or claim.first_seen_at or now,
        half_life_days=config.dreaming_recency_half_life_days,
    )
    consolidation_score = _consolidation_score(evidence)
    conceptual_richness_score = _conceptual_richness_score(claim, evidence)
    light_boost, rem_boost = _phase_boosts(claim, config)
    revalidation_score, revalidation_passed, revalidation_reason = _revalidate_claim(
        store, claim, evidence
    )
    contradiction_penalty = 0.0
    contradiction_target = claim.metadata.get("contradicts_claim_key")
    if contradiction_target and contradiction_target in claims_by_key:
        contradiction_penalty = 0.4
    volatility_penalty = _volatility_penalty(claim, claims_by_key, now=now)
    return (
        {
            "relevance_score": relevance_score,
            "frequency_score": frequency_score,
            "query_diversity_score": query_diversity_score,
            "recency_score": recency_score,
            "consolidation_score": consolidation_score,
            "conceptual_richness_score": conceptual_richness_score,
            "light_phase_boost": light_boost,
            "rem_phase_boost": rem_boost,
            "revalidation_score": revalidation_score,
            "contradiction_penalty": contradiction_penalty,
            "volatility_penalty": volatility_penalty,
            "revalidation_reason": revalidation_reason,
        },
        revalidation_passed,
    )


def _combine_score(components: dict[str, float | str]) -> float:
    base = (
        0.30 * float(components["relevance_score"])
        + 0.24 * float(components["frequency_score"])
        + 0.15 * float(components["query_diversity_score"])
        + 0.15 * float(components["recency_score"])
        + 0.10 * float(components["consolidation_score"])
        + 0.06 * float(components["conceptual_richness_score"])
    )
    base += float(components["light_phase_boost"])
    base += float(components["rem_phase_boost"])
    base -= float(components["contradiction_penalty"])
    base -= float(components["volatility_penalty"])
    return max(0.0, min(1.0, base))


def _evaluate_promotion_gates(
    claim: MemoryClaim,
    evidence: list[ClaimEvidence],
    *,
    score: float,
    components: dict[str, float | str],
    revalidation_passed: bool,
    config: RepoMemoryConfig,
    now: datetime,
) -> list[str]:
    failures: list[str] = []
    if score < config.dreaming_promotion_min_score:
        failures.append(f"score<{config.dreaming_promotion_min_score}")
    evidence_count = len(evidence)
    if evidence_count < config.dreaming_promotion_min_evidence_count:
        failures.append(
            f"evidence_count<{config.dreaming_promotion_min_evidence_count}"
        )
    source_diversity = _source_diversity(evidence)
    if source_diversity < config.dreaming_promotion_min_source_diversity:
        failures.append(
            f"source_diversity<{config.dreaming_promotion_min_source_diversity}"
        )
    if not revalidation_passed:
        failures.append("revalidation_failed")
    if float(components["contradiction_penalty"]) > 0:
        failures.append("active_contradiction")
    last_seen = claim.last_seen_at or claim.first_seen_at or now
    age_days = max((now - last_seen).total_seconds() / 86400.0, 0.0)
    if age_days > config.dreaming_max_age_days:
        failures.append(f"age_days>{config.dreaming_max_age_days}")
    return failures


def _resolve_status(
    *,
    claim: MemoryClaim,
    score: float,
    components: dict[str, float | str],
    revalidation_passed: bool,
    gate_failures: list[str],
    config: RepoMemoryConfig,
) -> ClaimStatus:
    if float(components["contradiction_penalty"]) > 0:
        return ClaimStatus.CONTESTED
    if not revalidation_passed and claim.revalidation_mode == RevalidationMode.STRICT_LIVE_STATE:
        return ClaimStatus.STALE
    if not gate_failures:
        return ClaimStatus.PROMOTED
    if score >= max(0.6, config.dreaming_promotion_min_score - 0.2) and (
        revalidation_passed or claim.revalidation_mode == RevalidationMode.EVIDENCE_ONLY
    ):
        return ClaimStatus.ACTIVE
    return ClaimStatus.CANDIDATE


def _relevance_score(evidence: list[ClaimEvidence]) -> float:
    if not evidence:
        return 0.0
    weights = [max(0.0, min(1.0, item.weight)) for item in evidence]
    return sum(weights) / len(weights)


def _frequency_score(evidence: list[ClaimEvidence]) -> float:
    if not evidence:
        return 0.0
    # Logarithmic accumulation so that the 3rd piece of evidence carries real
    # weight without letting an arbitrary pile of repeats saturate the signal.
    return min(math.log2(len(evidence) + 1) / 3.0, 1.0)


def _query_diversity_score(evidence: list[ClaimEvidence]) -> float:
    if not evidence:
        return 0.0
    threads = {item.source_thread_id for item in evidence if item.source_thread_id}
    paths = {item.source_path for item in evidence if item.source_path}
    entities = {item.source_entity_id for item in evidence if item.source_entity_id}
    dimensions = [threads, paths, entities]
    contributions: list[float] = []
    for dim in dimensions:
        if not dim:
            contributions.append(0.5)
        else:
            contributions.append(min(len(dim) / 3.0, 1.0))
    return sum(contributions) / len(contributions)


def _recency_score(now: datetime, last_seen_at: datetime, *, half_life_days: float) -> float:
    age_days = max((now - last_seen_at).total_seconds() / 86400.0, 0.0)
    if half_life_days <= 0:
        return 1.0 if age_days == 0 else 0.0
    return max(0.0, min(1.0, 0.5 ** (age_days / half_life_days)))


def _consolidation_score(evidence: list[ClaimEvidence]) -> float:
    if not evidence:
        return 0.0
    runs = {item.run_id for item in evidence if item.run_id}
    return min(len(runs) / 3.0, 1.0) if runs else 0.0


def _conceptual_richness_score(claim: MemoryClaim, evidence: list[ClaimEvidence]) -> float:
    if not claim.text:
        return 0.0
    token_count = len(_TOKEN_RE.findall(claim.text))
    entity_breadth = len({item.source_entity_id for item in evidence if item.source_entity_id})
    tokens_component = min(token_count / 24.0, 1.0)
    entity_component = min(entity_breadth / 3.0, 1.0)
    return 0.7 * tokens_component + 0.3 * entity_component


def _phase_boosts(claim: MemoryClaim, config: RepoMemoryConfig) -> tuple[float, float]:
    boosts = claim.metadata.get(_PHASE_BOOST_METADATA_KEY, {}) or {}
    light_hits = int(boosts.get("light", 0))
    rem_hits = int(boosts.get("rem", 0))
    light = min(config.dreaming_light_phase_boost_cap, light_hits * 0.01)
    rem = min(config.dreaming_rem_phase_boost_cap, rem_hits * 0.02)
    return light, rem


def _revalidate_claim(
    store: object,
    claim: MemoryClaim,
    evidence: list[ClaimEvidence],
) -> tuple[float, bool, str]:
    if claim.revalidation_mode == RevalidationMode.EVIDENCE_ONLY:
        passed = bool(evidence)
        return (1.0 if passed else 0.0, passed, "evidence-present" if passed else "no-evidence")
    if claim.revalidation_mode == RevalidationMode.MANUAL_REVIEW:
        return (0.5, False, "manual-review")
    if claim.scope_kind == ClaimScopeKind.PATH:
        exists = store.get_file(claim.repo, claim.scope_ref) is not None
        return (1.0 if exists else 0.0, exists, "path-exists" if exists else "path-missing")
    if claim.scope_kind == ClaimScopeKind.ENTITY:
        exists = store.get_entity(claim.scope_ref) is not None
        return (1.0 if exists else 0.0, exists, "entity-exists" if exists else "entity-missing")
    exists = store.get_sync_state(claim.repo).get("last_observed_seq", 0) > 0
    return (1.0 if exists else 0.0, exists, "repo-seen" if exists else "repo-empty")


def _volatility_penalty(
    claim: MemoryClaim,
    claims_by_key: dict[str, MemoryClaim],
    *,
    now: datetime,
) -> float:
    if claim.claim_kind != ClaimKind.HIGH_IMPACT_CHANGE:
        return 0.0
    siblings = 0
    for other in claims_by_key.values():
        if other.claim_key == claim.claim_key:
            continue
        if other.claim_kind != claim.claim_kind:
            continue
        if other.scope_kind != claim.scope_kind or other.scope_ref != claim.scope_ref:
            continue
        last_seen = other.last_seen_at or other.first_seen_at or now
        if (now - last_seen).total_seconds() <= 72 * 3600:
            siblings += 1
    return min(siblings * 0.15, 0.45)


def _source_diversity(evidence: list[ClaimEvidence]) -> int:
    pool: set[tuple[str, str]] = set()
    for item in evidence:
        if item.source_thread_id:
            pool.add(("thread", item.source_thread_id))
        if item.source_path:
            pool.add(("path", item.source_path))
        if item.source_entity_id:
            pool.add(("entity", item.source_entity_id))
    if not pool:
        return len(evidence)
    return len(pool)


def _event_importance(event: RepoEvent) -> float:
    if event.kind == RepoEventKind.WATCHOUT:
        return 1.0
    if event.kind == RepoEventKind.DECISION:
        return 0.9
    if event.kind == RepoEventKind.EDIT:
        return 0.8
    return 0.7


def _build_overlay_claims(
    store: object,
    repo: str,
    *,
    snapshot: RepoCoreSnapshot,
    config: RepoMemoryConfig,
    focus_paths: list[str],
    focus_entities: list[str],
    claims_by_key: dict[str, MemoryClaim],
) -> list[MemoryClaim]:
    recent_events = [
        event
        for event in store.list_repo_events(repo)
        if event.observed_seq > snapshot.source_watermark
    ]
    overlay: list[MemoryClaim] = []
    for candidate, _evidence in build_candidate_claims_from_events(
        repo,
        recent_events,
        now=datetime.now(UTC),
        config=config,
    ):
        if candidate.claim_key in snapshot.source_claim_keys:
            continue
        related = store.find_related_claims(
            repo,
            candidate.embedding,
            claim_kind=candidate.claim_kind,
            scope_kind=candidate.scope_kind,
            scope_ref=candidate.scope_ref,
            limit=1,
        )
        if (
            related
            and related[0][1] >= config.dreaming_overlay_similarity_threshold
            and related[0][0].claim_key in snapshot.source_claim_keys
        ):
            continue
        is_focus_relevant = (
            candidate.metadata.get("path") in focus_paths
            or candidate.metadata.get("entity_id") in focus_entities
        )
        priority = 1.0 if is_focus_relevant else _event_importance_from_claim(candidate)
        candidate.score = priority
        overlay.append(candidate)
    overlay.sort(key=lambda claim: (-claim.score, claim.claim_key))
    return overlay[: config.dreaming_overlay_max_items]


def _apply_overlay_to_blocks(
    snapshot_blocks: list[RepoCoreBlock],
    overlay_claims: list[MemoryClaim],
) -> list[RepoCoreBlock]:
    block_map = {block.label: block for block in snapshot_blocks}
    for claim in overlay_claims:
        label = _SNAPSHOT_BLOCKS.get(claim.claim_kind)
        if label is None:
            continue
        block = block_map.get(label)
        if block is None:
            block = RepoCoreBlock(
                label=label,
                description=_BLOCK_DESCRIPTIONS[label],
                value="",
                token_budget=120,
            )
            block_map[label] = block
            snapshot_blocks.append(block)
        lines = [line for line in block.value.splitlines() if line]
        lines.append(f"Fresh: {claim.text}")
        block.value = _trim_lines(lines, block.token_budget)
    snapshot_blocks.sort(key=lambda block: block.label)
    return snapshot_blocks


def _trim_lines(lines: list[str], budget: int) -> str:
    words: list[str] = []
    for line in lines:
        tokens = line.split()
        if len(words) + len(tokens) > budget:
            remaining = max(budget - len(words), 0)
            words.extend(tokens[:remaining])
            break
        words.extend(tokens)
    return " ".join(words) if words else "No repository memory available."


def _event_importance_from_claim(claim: MemoryClaim) -> float:
    if claim.claim_kind == ClaimKind.WATCHOUT:
        return 1.0
    if claim.claim_kind == ClaimKind.DESIGN_DECISION:
        return 0.9
    return 0.8
