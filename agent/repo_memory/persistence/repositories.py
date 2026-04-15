from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..config import RepoMemoryConfig
from ..domain import (
    ClaimEvidence,
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    CodeEntity,
    DreamingLease,
    DreamRun,
    EntityRevision,
    FileRevision,
    MemoryClaim,
    RepoCoreBlock,
    RepoCoreSnapshot,
    RepoEvent,
    RepoFile,
)
from ..embeddings import build_embedding_provider, cosine_similarity
from .models import MemoryMetadata, build_metadata
from .postgres import PostgresRepoMemoryStore


@dataclass(slots=True)
class InMemoryRepoMemoryStore:
    metadata: MemoryMetadata = field(default_factory=build_metadata)
    _files: dict[tuple[str, str], RepoFile] = field(init=False, default_factory=dict)
    _entities: dict[str, CodeEntity] = field(init=False, default_factory=dict)
    _events: dict[str, list[RepoEvent]] = field(
        init=False,
        default_factory=lambda: defaultdict(list),
    )
    _core_blocks: dict[str, dict[str, RepoCoreBlock]] = field(
        init=False,
        default_factory=lambda: defaultdict(dict),
    )
    _sync_state: dict[str, dict[str, int]] = field(
        init=False,
        default_factory=lambda: defaultdict(
            lambda: {"last_observed_seq": 0, "last_compiled_seq": 0, "dreaming_cursor": 0}
        ),
    )
    _lineage: list[dict[str, Any]] = field(init=False, default_factory=list)
    _claims: dict[tuple[str, str], MemoryClaim] = field(init=False, default_factory=dict)
    _claim_source_index: dict[tuple[str, str], str] = field(init=False, default_factory=dict)
    _claim_evidence: dict[tuple[str, str], list[ClaimEvidence]] = field(
        init=False, default_factory=lambda: defaultdict(list)
    )
    _snapshots: dict[str, list[RepoCoreSnapshot]] = field(
        init=False, default_factory=lambda: defaultdict(list)
    )
    _dream_runs: dict[str, list[DreamRun]] = field(
        init=False, default_factory=lambda: defaultdict(list)
    )
    _leases: dict[str, DreamingLease] = field(init=False, default_factory=dict)

    def list_repositories(self) -> list[str]:
        repos: set[str] = set(self._events)
        repos.update(repo for repo, _path in self._files)
        repos.update(entity.repo for entity in self._entities.values())
        repos.update(repo for repo, _claim_key in self._claims)
        repos.update(self._core_blocks)
        repos.update(self._snapshots)
        repos.update(self._dream_runs)
        repos.update(self._sync_state)
        return sorted(repos)

    def upsert_file_revision(self, revision: FileRevision) -> None:
        key = (revision.repo, revision.path)
        current = self._files.get(key)
        if current is None:
            self._files[key] = RepoFile(
                repo=revision.repo,
                path=revision.path,
                language=revision.language,
                current_revision=revision,
            )
        else:
            current.observe(revision)
        self._sync_state[revision.repo]["last_observed_seq"] = max(
            self._sync_state[revision.repo]["last_observed_seq"],
            revision.observed_seq,
        )

    def get_file(self, repo: str, path: str) -> RepoFile | None:
        return self._files.get((repo, path))

    def upsert_entity_revision(self, revision: EntityRevision) -> None:
        current = self._entities.get(revision.entity_id)
        if current is None:
            self._entities[revision.entity_id] = CodeEntity(
                entity_id=revision.entity_id,
                repo=revision.repo,
                path=revision.path,
                language=revision.language,
                kind=revision.kind,
                current_revision=revision,
            )
        else:
            current.observe(revision)
        self._sync_state[revision.repo]["last_observed_seq"] = max(
            self._sync_state[revision.repo]["last_observed_seq"],
            revision.observed_seq,
        )

    def get_entity(self, entity_id: str) -> CodeEntity | None:
        return self._entities.get(entity_id)

    def iter_entities(self, repo: str) -> list[EntityRevision]:
        revisions: list[EntityRevision] = []
        for entity in self._entities.values():
            if entity.repo == repo:
                revisions.append(entity.current_revision)
        return revisions

    def append_repo_event(self, event: RepoEvent) -> None:
        self._events[event.repo].append(event)
        self._sync_state[event.repo]["last_observed_seq"] = max(
            self._sync_state[event.repo]["last_observed_seq"],
            event.observed_seq,
        )

    def list_repo_events(self, repo: str) -> list[RepoEvent]:
        return list(self._events.get(repo, []))

    def set_core_block(self, repo: str, block: RepoCoreBlock) -> None:
        self._core_blocks[repo][block.label] = block

    def list_core_blocks(self, repo: str) -> list[RepoCoreBlock]:
        return list(self._core_blocks.get(repo, {}).values())

    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        self._sync_state[repo]["last_compiled_seq"] = observed_seq

    def get_sync_state(self, repo: str) -> dict[str, int]:
        return dict(self._sync_state[repo])

    def upsert_claim(self, claim: MemoryClaim) -> MemoryClaim:
        key = (claim.repo, claim.claim_key)
        current = self._claims.get(key)
        if current is None:
            self._claims[key] = claim
            self._claim_source_index[(claim.repo, claim.source_identity_key)] = claim.claim_key
            return claim
        current.text = claim.text
        current.normalized_text = claim.normalized_text
        current.status = claim.status
        current.score = claim.score
        current.score_components = dict(claim.score_components)
        current.last_seen_at = claim.last_seen_at
        current.last_revalidated_at = claim.last_revalidated_at
        current.revalidation_mode = claim.revalidation_mode
        current.embedding = list(claim.embedding)
        current.embedding_provider = claim.embedding_provider
        current.embedding_dimensions = claim.embedding_dimensions
        current.embedding_version = claim.embedding_version
        current.metadata = dict(claim.metadata)
        if not current.source_identity_key and claim.source_identity_key:
            current.source_identity_key = claim.source_identity_key
            self._claim_source_index[(claim.repo, claim.source_identity_key)] = claim.claim_key
        return current

    def get_claim_by_source_identity(self, repo: str, source_identity_key: str) -> MemoryClaim | None:
        claim_key = self._claim_source_index.get((repo, source_identity_key))
        if claim_key is None:
            return None
        return self._claims.get((repo, claim_key))

    def list_claims(
        self,
        repo: str,
        statuses: set[ClaimStatus] | None = None,
    ) -> list[MemoryClaim]:
        claims = [claim for (claim_repo, _), claim in self._claims.items() if claim_repo == repo]
        if statuses is not None:
            claims = [claim for claim in claims if claim.status in statuses]
        claims.sort(
            key=lambda claim: (
                claim.score or 0.0,
                claim.last_seen_at or datetime.min,
                claim.claim_key,
            ),
            reverse=True,
        )
        return claims

    def attach_claim_evidence(self, claim_key: str, evidence: ClaimEvidence) -> None:
        claim = self._claims.get((evidence.repo, claim_key))
        if claim is None:
            raise KeyError(f"Unknown claim: {evidence.repo}:{claim_key}")
        bucket = self._claim_evidence[(evidence.repo, claim_key)]
        if any(item.evidence_id == evidence.evidence_id for item in bucket):
            return
        bucket.append(evidence)
        self._claim_source_index[(evidence.repo, evidence.evidence_ref)] = claim_key

    def list_claim_evidence(self, repo: str, claim_key: str) -> list[ClaimEvidence]:
        return list(self._claim_evidence.get((repo, claim_key), []))

    def find_related_claims(
        self,
        repo: str,
        query_embedding: list[float],
        *,
        claim_kind: ClaimKind | None = None,
        scope_kind: ClaimScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int = 5,
    ) -> list[tuple[MemoryClaim, float]]:
        if not query_embedding:
            return []
        ranked: list[tuple[MemoryClaim, float]] = []
        for claim in self.list_claims(repo):
            if claim_kind is not None and claim.claim_kind != claim_kind:
                continue
            if scope_kind is not None and claim.scope_kind != scope_kind:
                continue
            if scope_ref is not None and claim.scope_ref != scope_ref:
                continue
            similarity = cosine_similarity(query_embedding, claim.embedding)
            if similarity <= 0:
                continue
            ranked.append((claim, similarity))
        ranked.sort(key=lambda item: (-item[1], -item[0].score, item[0].claim_key))
        return ranked[:limit]

    def create_repo_core_snapshot(self, snapshot: RepoCoreSnapshot) -> None:
        snapshots = self._snapshots[snapshot.repo]
        for index, current in enumerate(snapshots):
            if current.snapshot_id == snapshot.snapshot_id:
                snapshots[index] = snapshot
                break
        else:
            snapshots.append(snapshot)
        snapshots.sort(key=lambda item: (item.compiled_at, item.snapshot_id))

    def get_latest_repo_core_snapshot(self, repo: str) -> RepoCoreSnapshot | None:
        snapshots = self._snapshots.get(repo, [])
        if not snapshots:
            return None
        return snapshots[-1]

    def create_dream_run(self, run: DreamRun) -> None:
        self._dream_runs[run.repo].append(run)

    def finalize_dream_run(self, run: DreamRun) -> None:
        runs = self._dream_runs[run.repo]
        for index, current in enumerate(runs):
            if current.run_id == run.run_id:
                runs[index] = run
                break
        else:
            runs.append(run)

    def list_dream_runs(self, repo: str) -> list[DreamRun]:
        return list(self._dream_runs.get(repo, []))

    def acquire_dreaming_lease(
        self,
        repo: str,
        worker_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> bool:
        lease = self._leases.get(repo)
        if lease and lease.worker_id != worker_id and lease.expires_at > now:
            return False
        self._leases[repo] = DreamingLease(
            repo=repo,
            worker_id=worker_id,
            expires_at=now if ttl_seconds <= 0 else now + timedelta(seconds=ttl_seconds),
        )
        return True

    def release_dreaming_lease(self, repo: str, worker_id: str) -> None:
        lease = self._leases.get(repo)
        if lease and lease.worker_id == worker_id:
            self._leases.pop(repo, None)

    def get_dreaming_cursor(self, repo: str) -> int:
        return self._sync_state[repo]["dreaming_cursor"]

    def set_dreaming_cursor(self, repo: str, watermark: int) -> None:
        self._sync_state[repo]["dreaming_cursor"] = max(
            self._sync_state[repo]["dreaming_cursor"], watermark
        )

    def record_lineage(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        self._lineage.append(
            {
                "entity_id": entity_id,
                "predecessor_id": predecessor_id,
                "reason": reason,
                "confidence": confidence,
            }
        )
        entity = self._entities.get(entity_id)
        if entity and predecessor_id not in entity.predecessor_ids:
            entity.predecessor_ids.append(predecessor_id)

    def list_lineage(self) -> list[dict[str, Any]]:
        return list(self._lineage)


def create_repo_memory_store(config: RepoMemoryConfig) -> InMemoryRepoMemoryStore | PostgresRepoMemoryStore:
    if config.resolved_backend() == "postgres":
        if not config.database_url:
            raise ValueError("REPO_MEMORY_DATABASE_URL is required for postgres repo memory")
        return PostgresRepoMemoryStore(
            database_url=config.database_url,
            embedding_provider=build_embedding_provider(config),
        )
    return InMemoryRepoMemoryStore()
