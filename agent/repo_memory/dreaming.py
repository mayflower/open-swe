from __future__ import annotations

import re
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
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

        new_events = [event for event in store.list_repo_events(repo) if event.observed_seq > cursor_before]
        candidates = build_candidate_claims_from_events(
            repo,
            new_events,
            now=run_now,
            config=config,
        )
        run.signal_count = len(new_events)
        run.claim_candidate_count = len(candidates)

        merged_count = 0
        for candidate, evidence in candidates:
            claim, merged = upsert_candidate_claim(store, candidate, evidence, config=config)
            evidence.claim_key = claim.claim_key
            evidence.run_id = run.run_id
            store.attach_claim_evidence(claim.claim_key, evidence)
            if merged:
                merged_count += 1
        run.merged_count = merged_count

        promoted_count = score_and_transition_claims(store, repo, now=run_now, config=config)
        run.promoted_count = promoted_count

        snapshot = compile_repo_core_snapshot(
            store,
            repo,
            source_watermark=watermark,
            now=run_now,
            config=config,
        )
        if snapshot is not None:
            store.create_repo_core_snapshot(snapshot)
            run.snapshot_id = snapshot.snapshot_id

        store.set_dreaming_cursor(repo, watermark)
        run.cursor_after = watermark
        run.status = "succeeded"
        run.finished_at = run_now
        run.summary = {
            "signal_count": run.signal_count,
            "claim_candidate_count": run.claim_candidate_count,
            "merged_count": run.merged_count,
            "promoted_count": run.promoted_count,
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
    existing = store.get_claim_by_source_identity(candidate.repo, candidate.source_identity_key)
    if existing is not None:
        updated = _merge_claim(existing, candidate)
        stored = store.upsert_claim(updated)
        return stored, False

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
        updated = _merge_claim(target, candidate)
        merged_sources = list(updated.metadata.get("merged_source_identities", []))
        if candidate.source_identity_key not in merged_sources:
            merged_sources.append(candidate.source_identity_key)
        updated.metadata["merged_source_identities"] = merged_sources
        stored = store.upsert_claim(updated)
        return stored, True

    stored = store.upsert_claim(candidate)
    return stored, False


def score_and_transition_claims(
    store: object,
    repo: str,
    *,
    now: datetime,
    config: RepoMemoryConfig,
) -> int:
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
        )
        score = max(
            0.0,
            min(
                1.0,
                (
                    0.35 * components["frequency_score"]
                    + 0.25 * components["recency_score"]
                    + 0.20 * components["diversity_score"]
                    + 0.10 * components["importance_score"]
                    + 0.10 * components["revalidation_score"]
                    - components["contradiction_penalty"]
                    - components["volatility_penalty"]
                ),
            ),
        )
        status = ClaimStatus.CANDIDATE
        if components["contradiction_penalty"] > 0:
            status = ClaimStatus.CONTESTED
        elif not revalidation_passed and claim.revalidation_mode == RevalidationMode.STRICT_LIVE_STATE:
            status = ClaimStatus.STALE
        elif score >= 0.78 and revalidation_passed:
            status = ClaimStatus.PROMOTED
        elif score >= 0.60 and (revalidation_passed or claim.revalidation_mode == RevalidationMode.EVIDENCE_ONLY):
            status = ClaimStatus.ACTIVE
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


def _score_components(
    store: object,
    claim: MemoryClaim,
    evidence: list[ClaimEvidence],
    *,
    claims_by_key: dict[str, MemoryClaim],
    now: datetime,
) -> tuple[dict[str, float | str], bool]:
    evidence_count = len(evidence)
    frequency_score = min(evidence_count / 2.0, 1.0)
    recency_score = _recency_score(now, claim.last_seen_at or claim.first_seen_at or now)
    diversity_score = _diversity_score(evidence)
    importance_score = min(max((item.weight for item in evidence), default=0.0), 1.0)
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
            "frequency_score": frequency_score,
            "recency_score": recency_score,
            "diversity_score": diversity_score,
            "importance_score": importance_score,
            "revalidation_score": revalidation_score,
            "contradiction_penalty": contradiction_penalty,
            "volatility_penalty": volatility_penalty,
            "revalidation_reason": revalidation_reason,
        },
        revalidation_passed,
    )


def _recency_score(now: datetime, last_seen_at: datetime) -> float:
    age_hours = max((now - last_seen_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.75
    if age_hours <= 168:
        return 0.5
    return 0.25


def _diversity_score(evidence: list[ClaimEvidence]) -> float:
    dimensions = {
        "runs": {item.run_id for item in evidence if item.run_id},
        "threads": {item.source_thread_id for item in evidence if item.source_thread_id},
        "paths": {item.source_path for item in evidence if item.source_path},
        "entities": {item.source_entity_id for item in evidence if item.source_entity_id},
    }
    parts: list[float] = []
    for values in dimensions.values():
        if not values:
            parts.append(0.5)
        else:
            parts.append(min(len(values) / 2.0, 1.0))
    return sum(parts) / len(parts) if parts else 0.0


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
