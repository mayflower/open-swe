from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import asyncpg

from ..domain import (
    ClaimEvidence,
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    CodeEntity,
    DreamRun,
    EntityKind,
    EntityRevision,
    FileRevision,
    MemoryClaim,
    RepoCoreBlock,
    RepoCoreSnapshot,
    RepoEvent,
    RepoEventKind,
    RepoFile,
    RevalidationMode,
)
from ..embeddings import EmbeddingProvider
from .migrations import validate_repo_memory_schema_async
from .models import MemoryMetadata, build_metadata
from .pool import arun, get_pool, run_async

T = TypeVar("T")


# Schema validation is process-global: once one ``PostgresRepoMemoryStore``
# instance for ``database_url`` has confirmed the schema, every other
# instance in the same process can short-circuit. Without this, parallel
# agents each pay the validation round-trip on first use.
_SCHEMA_READY_BY_URL: set[str] = set()
_SCHEMA_READY_LOCK = threading.Lock()


@dataclass(slots=True)
class VectorSearchHit:
    entity: EntityRevision
    similarity: float


def _merge_claim_objects(existing: MemoryClaim, candidate: MemoryClaim) -> MemoryClaim:
    """Mirror of ``dreaming._merge_claim`` so the postgres dedup path doesn't
    need to import dreaming."""
    return replace(
        existing,
        text=candidate.text,
        normalized_text=candidate.normalized_text,
        last_seen_at=candidate.last_seen_at,
        embedding=candidate.embedding,
        metadata={**existing.metadata, **candidate.metadata},
    )


def _merge_with_source(target: MemoryClaim, candidate: MemoryClaim) -> MemoryClaim:
    merged = _merge_claim_objects(target, candidate)
    sources = list(merged.metadata.get("merged_source_identities", []))
    if candidate.source_identity_key and candidate.source_identity_key not in sources:
        sources.append(candidate.source_identity_key)
    merged.metadata["merged_source_identities"] = sources
    return merged


def _token_set(text: str) -> set[str]:
    return {token for token in text.split() if token}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _find_jaccard_target(
    rows: list[asyncpg.Record], candidate: MemoryClaim, *, threshold: float
) -> MemoryClaim | None:
    candidate_tokens = _token_set(candidate.normalized_text)
    if not candidate_tokens:
        return None
    best: tuple[MemoryClaim, float] | None = None
    for row in rows:
        claim = _claim_from_row(row)
        if claim.claim_key == candidate.claim_key:
            continue
        similarity = _jaccard(candidate_tokens, _token_set(claim.normalized_text))
        if similarity >= threshold and (best is None or similarity > best[1]):
            best = (claim, similarity)
    return best[0] if best is not None else None


def _advisory_lock_keys(repo: str, source_identity_key: str) -> tuple[int, int]:
    """Two 32-bit ints used as ``pg_advisory_xact_lock(int, int)`` arguments.

    Hashing ``(repo, source_identity_key)`` produces a stable lock identifier
    that two writers racing on the same claim source will collide on, so the
    Light-phase dedup transaction can serialize without a wider table lock.
    Both halves are decoded as signed because ``pg_advisory_xact_lock(int,
    int)`` takes int4 — an unsigned decode would overflow when the high bit
    is set.
    """
    digest = hashlib.blake2b(
        f"{repo}|{source_identity_key}".encode(),
        digest_size=8,
    ).digest()
    high = int.from_bytes(digest[:4], "big", signed=True)
    low = int.from_bytes(digest[4:], "big", signed=True)
    return (high, low)


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _parse_pgvector(raw: object) -> list[float]:
    """Normalize pgvector column values into ``list[float]``.

    asyncpg returns pgvector columns as their text representation
    (``"[0.1,0.2,…]"``) when no custom codec is registered. This helper makes
    loaders resilient to both the string and list cases without forcing every
    caller to pay the codec-registration cost.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return []
        if trimmed.startswith("[") and trimmed.endswith("]"):
            trimmed = trimmed[1:-1]
        if not trimmed:
            return []
        return [float(part) for part in trimmed.split(",") if part]
    return [float(value) for value in raw]


def _entity_revision_from_row(row: asyncpg.Record) -> EntityRevision:
    return EntityRevision(
        entity_id=row["entity_id"],
        repo=row["repo"],
        path=row["path"],
        language=row["language"],
        kind=EntityKind(row["kind"]),
        name=row["name"],
        qualified_name=row["qualified_name"],
        observed_seq=row["observed_seq"],
        signature=row["signature"] or "",
        parent_qualified_name=row["parent_qualified_name"],
        docstring=row["docstring"] or "",
        comment=row["comment"] or "",
        body=row["body"] or "",
        retrieval_text=row["retrieval_text"] or "",
        start_line=row["start_line"],
        end_line=row["end_line"],
    )


def _file_revision_from_row(row: asyncpg.Record) -> FileRevision:
    return FileRevision(
        repo=row["repo"],
        path=row["path"],
        language=row["language"],
        observed_seq=row["observed_seq"],
        content=row["content"],
        summary=row["summary"] or "",
    )


def _repo_event_from_row(row: asyncpg.Record) -> RepoEvent:
    evidence_refs = row["evidence_refs"]
    metadata = row["metadata"]
    if isinstance(evidence_refs, str):
        evidence_refs = json.loads(evidence_refs)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return RepoEvent(
        repo=row["repo"],
        event_id=row["event_id"],
        kind=RepoEventKind(row["kind"]),
        summary=row["summary"],
        observed_seq=row["observed_seq"],
        path=row["path"],
        entity_id=row["entity_id"],
        evidence_refs=list(evidence_refs or []),
        metadata=dict(metadata or {}),
    )


def _core_block_from_row(row: asyncpg.Record) -> RepoCoreBlock:
    return RepoCoreBlock(
        label=row["label"],
        description=row["description"],
        value=row["value"],
        token_budget=row["token_budget"],
        read_only=row["read_only"],
    )


def _claim_from_row(row: asyncpg.Record) -> MemoryClaim:
    score_components = row["score_components"] or {}
    metadata = row["metadata"] or {}
    embedding = _parse_pgvector(row["embedding"])
    if isinstance(score_components, str):
        score_components = json.loads(score_components)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return MemoryClaim(
        claim_id=row["claim_id"],
        claim_key=row["claim_key"],
        source_identity_key=row["source_identity_key"],
        repo=row["repo"],
        scope_kind=ClaimScopeKind(row["scope_kind"]),
        scope_ref=row["scope_ref"],
        claim_kind=ClaimKind(row["claim_kind"]),
        text=row["text"],
        normalized_text=row["normalized_text"],
        status=ClaimStatus(row["status"]),
        score=float(row["score"] or 0.0),
        score_components=dict(score_components),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_revalidated_at=row["last_revalidated_at"],
        revalidation_mode=RevalidationMode(row["revalidation_mode"]),
        embedding=embedding,
        embedding_provider=row["embedding_provider"] or "hashed",
        embedding_dimensions=row["embedding_dimensions"] or len(embedding),
        embedding_version=row["embedding_version"] or "v1",
        metadata=dict(metadata),
    )


def _claim_evidence_from_row(row: asyncpg.Record) -> ClaimEvidence:
    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return ClaimEvidence(
        evidence_id=row["evidence_id"],
        repo=row["repo"],
        claim_key=row["claim_key"],
        run_id=row["run_id"],
        evidence_kind=row["evidence_kind"],
        evidence_ref=row["evidence_ref"],
        evidence_text=row["evidence_text"] or "",
        weight=float(row["weight"] or 0.0),
        observed_at=row["observed_at"],
        source_thread_id=row["source_thread_id"],
        source_path=row["source_path"],
        source_entity_id=row["source_entity_id"],
        metadata=dict(metadata),
    )


def _snapshot_from_row(row: asyncpg.Record) -> RepoCoreSnapshot:
    blocks_payload = row["blocks"] or []
    source_claim_keys = row["source_claim_keys"] or []
    metadata = row["metadata"] or {}
    if isinstance(blocks_payload, str):
        blocks_payload = json.loads(blocks_payload)
    if isinstance(source_claim_keys, str):
        source_claim_keys = json.loads(source_claim_keys)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return RepoCoreSnapshot(
        snapshot_id=row["snapshot_id"],
        repo=row["repo"],
        compiled_at=row["compiled_at"],
        source_watermark=row["source_watermark"],
        blocks=[
            RepoCoreBlock(
                label=item["label"],
                description=item["description"],
                value=item["value"],
                token_budget=item["token_budget"],
                read_only=item.get("read_only", True),
            )
            for item in blocks_payload
        ],
        source_claim_keys=list(source_claim_keys),
        metadata=dict(metadata),
    )


def _dream_run_from_row(row: asyncpg.Record) -> DreamRun:
    summary = row["summary"] or {}
    if isinstance(summary, str):
        summary = json.loads(summary)
    return DreamRun(
        run_id=row["run_id"],
        repo=row["repo"],
        run_kind=row["run_kind"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        worker_id=row["worker_id"],
        cursor_before=row["cursor_before"] or 0,
        cursor_after=row["cursor_after"] or 0,
        signal_count=row["signal_count"] or 0,
        claim_candidate_count=row["claim_candidate_count"] or 0,
        merged_count=row["merged_count"] or 0,
        promoted_count=row["promoted_count"] or 0,
        snapshot_id=row["snapshot_id"],
        summary=dict(summary),
    )


@dataclass(slots=True)
class PostgresRepoMemoryStore:
    database_url: str
    embedding_provider: EmbeddingProvider
    metadata: MemoryMetadata = field(default_factory=build_metadata)
    _schema_ready: bool = field(init=False, default=False)
    _schema_lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def list_repositories(self) -> list[str]:
        return self._execute(self._list_repositories_op())

    async def alist_repositories(self) -> list[str]:
        return await self._aexecute(self._list_repositories_op())

    async def _list_repositories_op(self) -> list[str]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT repo
                FROM repositories
                ORDER BY repo ASC
                """
            )
        finally:
            await pool.release(conn)
        return [str(row["repo"]) for row in rows]

    def upsert_file_revision(self, revision: FileRevision) -> None:
        self._execute(self._upsert_file_revision_op(revision))

    async def aupsert_file_revision(self, revision: FileRevision) -> None:
        await self._aexecute(self._upsert_file_revision_op(revision))

    async def _upsert_file_revision_op(self, revision: FileRevision) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await self._ensure_repo_row(conn, revision.repo)
                await conn.execute(
                    """
                    INSERT INTO files (repo, path, language, current_observed_seq)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (repo, path) DO UPDATE
                    SET language = EXCLUDED.language,
                        current_observed_seq = GREATEST(
                            files.current_observed_seq,
                            EXCLUDED.current_observed_seq
                        )
                    """,
                    revision.repo,
                    revision.path,
                    revision.language,
                    revision.observed_seq,
                )
                await conn.execute(
                    """
                    INSERT INTO file_revisions
                        (repo, path, language, observed_seq, content, summary)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (repo, path, observed_seq) DO UPDATE
                    SET language = EXCLUDED.language,
                        content = EXCLUDED.content,
                        summary = EXCLUDED.summary
                    """,
                    revision.repo,
                    revision.path,
                    revision.language,
                    revision.observed_seq,
                    revision.content,
                    revision.summary,
                )
                await self._bump_last_observed_seq(conn, revision.repo, revision.observed_seq)
        finally:
            await pool.release(conn)

    def get_file(self, repo: str, path: str) -> RepoFile | None:
        return self._execute(self._get_file_op(repo, path))

    async def aget_file(self, repo: str, path: str) -> RepoFile | None:
        return await self._aexecute(self._get_file_op(repo, path))

    async def _get_file_op(self, repo: str, path: str) -> RepoFile | None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT repo, path, language, observed_seq, content, summary
                FROM file_revisions
                WHERE repo = $1 AND path = $2
                ORDER BY observed_seq ASC
                """,
                repo,
                path,
            )
        finally:
            await pool.release(conn)
        if not rows:
            return None
        revisions = [_file_revision_from_row(row) for row in rows]
        return RepoFile(
            repo=repo,
            path=path,
            language=revisions[-1].language,
            current_revision=revisions[-1],
            revisions=revisions,
        )

    def upsert_entity_revision(self, revision: EntityRevision) -> None:
        self.upsert_entity_revisions([revision])

    def upsert_entity_revisions(self, revisions: list[EntityRevision]) -> None:
        """Batch upsert. Embeddings are computed via ``embed_many`` so a flush
        of N entities issues one embedding API call instead of N.
        """
        if not revisions:
            return
        self._execute(self._upsert_entity_revisions_op(revisions))

    async def aupsert_entity_revisions(self, revisions: list[EntityRevision]) -> None:
        if not revisions:
            return
        await self._aexecute(self._upsert_entity_revisions_op(revisions))

    async def _upsert_entity_revisions_op(self, revisions: list[EntityRevision]) -> None:
        embeddings = self.embedding_provider.embed_many(
            [revision.retrieval_text for revision in revisions]
        )
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                repos_seen: set[str] = set()
                for revision, embedding in zip(revisions, embeddings, strict=False):
                    if revision.repo not in repos_seen:
                        await self._ensure_repo_row(conn, revision.repo)
                        repos_seen.add(revision.repo)
                    await self._perform_upsert_entity_revision(conn, revision, embedding)
                    await self._bump_last_observed_seq(conn, revision.repo, revision.observed_seq)
        finally:
            await pool.release(conn)

    async def _perform_upsert_entity_revision(
        self, conn: Any, revision: EntityRevision, embedding: list[float]
    ) -> None:
        await conn.execute(
            """
            INSERT INTO entities
                (entity_id, repo, path, language, kind, current_observed_seq, predecessor_ids)
            VALUES ($1, $2, $3, $4, $5, $6, '[]'::jsonb)
            ON CONFLICT (entity_id) DO UPDATE
            SET repo = EXCLUDED.repo,
                path = EXCLUDED.path,
                language = EXCLUDED.language,
                kind = EXCLUDED.kind,
                current_observed_seq = GREATEST(
                    entities.current_observed_seq,
                    EXCLUDED.current_observed_seq
                )
            """,
            revision.entity_id,
            revision.repo,
            revision.path,
            revision.language,
            revision.kind.value,
            revision.observed_seq,
        )
        await conn.execute(
            """
            INSERT INTO entity_revisions (
                entity_id,
                repo,
                path,
                language,
                kind,
                name,
                qualified_name,
                observed_seq,
                signature,
                parent_qualified_name,
                docstring,
                comment,
                body,
                retrieval_text,
                start_line,
                end_line,
                embedding
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14, $15, $16, $17::vector
            )
            ON CONFLICT (entity_id, observed_seq) DO UPDATE
            SET repo = EXCLUDED.repo,
                path = EXCLUDED.path,
                language = EXCLUDED.language,
                kind = EXCLUDED.kind,
                name = EXCLUDED.name,
                qualified_name = EXCLUDED.qualified_name,
                signature = EXCLUDED.signature,
                parent_qualified_name = EXCLUDED.parent_qualified_name,
                docstring = EXCLUDED.docstring,
                comment = EXCLUDED.comment,
                body = EXCLUDED.body,
                retrieval_text = EXCLUDED.retrieval_text,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                embedding = EXCLUDED.embedding
            """,
            revision.entity_id,
            revision.repo,
            revision.path,
            revision.language,
            revision.kind.value,
            revision.name,
            revision.qualified_name,
            revision.observed_seq,
            revision.signature,
            revision.parent_qualified_name,
            revision.docstring,
            revision.comment,
            revision.body,
            revision.retrieval_text,
            revision.start_line,
            revision.end_line,
            _vector_literal(embedding),
        )

    def get_entity(self, entity_id: str) -> CodeEntity | None:
        return self._execute(self._get_entity_op(entity_id))

    async def aget_entity(self, entity_id: str) -> CodeEntity | None:
        return await self._aexecute(self._get_entity_op(entity_id))

    async def _get_entity_op(self, entity_id: str) -> CodeEntity | None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            entity_row = await conn.fetchrow(
                """
                SELECT entity_id, repo, path, language, kind, current_observed_seq, predecessor_ids
                FROM entities
                WHERE entity_id = $1
                """,
                entity_id,
            )
            revisions_rows = await conn.fetch(
                """
                SELECT entity_id, repo, path, language, kind, name, qualified_name,
                       observed_seq, signature, parent_qualified_name, docstring,
                       comment, body, retrieval_text, start_line, end_line
                FROM entity_revisions
                WHERE entity_id = $1
                ORDER BY observed_seq ASC
                """,
                entity_id,
            )
        finally:
            await pool.release(conn)
        if entity_row is None or not revisions_rows:
            return None
        predecessor_ids = entity_row["predecessor_ids"]
        if isinstance(predecessor_ids, str):
            predecessor_ids = json.loads(predecessor_ids)
        revisions = [_entity_revision_from_row(row) for row in revisions_rows]
        current = next(
            (
                revision
                for revision in revisions
                if revision.observed_seq == entity_row["current_observed_seq"]
            ),
            revisions[-1],
        )
        return CodeEntity(
            entity_id=entity_row["entity_id"],
            repo=entity_row["repo"],
            path=entity_row["path"],
            language=entity_row["language"],
            kind=EntityKind(entity_row["kind"]),
            current_revision=current,
            revisions=revisions,
            predecessor_ids=list(predecessor_ids or []),
        )

    def iter_entities(self, repo: str) -> list[EntityRevision]:
        return self._execute(self._iter_entities_op(repo))

    async def aiter_entities(self, repo: str) -> list[EntityRevision]:
        return await self._aexecute(self._iter_entities_op(repo))

    async def _iter_entities_op(self, repo: str) -> list[EntityRevision]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT er.entity_id, er.repo, er.path, er.language, er.kind, er.name,
                       er.qualified_name, er.observed_seq, er.signature,
                       er.parent_qualified_name, er.docstring, er.comment,
                       er.body, er.retrieval_text, er.start_line, er.end_line
                FROM entity_revisions er
                JOIN entities e
                  ON e.entity_id = er.entity_id
                 AND e.current_observed_seq = er.observed_seq
                WHERE e.repo = $1
                ORDER BY er.observed_seq DESC, er.qualified_name ASC
                """,
                repo,
            )
        finally:
            await pool.release(conn)
        return [_entity_revision_from_row(row) for row in rows]

    def append_repo_event(self, event: RepoEvent) -> None:
        self._execute(self._append_repo_event_op(event))

    async def aappend_repo_event(self, event: RepoEvent) -> None:
        await self._aexecute(self._append_repo_event_op(event))

    async def _append_repo_event_op(self, event: RepoEvent) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await self._ensure_repo_row(conn, event.repo)
                await conn.execute(
                    """
                    INSERT INTO repo_events (
                        event_id,
                        repo,
                        kind,
                        summary,
                        observed_seq,
                        path,
                        entity_id,
                        evidence_refs,
                        metadata
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event.event_id,
                    event.repo,
                    event.kind.value,
                    event.summary,
                    event.observed_seq,
                    event.path,
                    event.entity_id,
                    json.dumps(event.evidence_refs),
                    json.dumps(event.metadata),
                )
                await self._bump_last_observed_seq(conn, event.repo, event.observed_seq)
        finally:
            await pool.release(conn)

    def list_repo_events(self, repo: str) -> list[RepoEvent]:
        return self._execute(self._list_repo_events_op(repo))

    async def alist_repo_events(self, repo: str) -> list[RepoEvent]:
        return await self._aexecute(self._list_repo_events_op(repo))

    async def _list_repo_events_op(self, repo: str) -> list[RepoEvent]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT repo, event_id, kind, summary, observed_seq, path, entity_id,
                       evidence_refs, metadata
                FROM repo_events
                WHERE repo = $1
                ORDER BY observed_seq ASC, event_id ASC
                """,
                repo,
            )
        finally:
            await pool.release(conn)
        return [_repo_event_from_row(row) for row in rows]

    def set_core_block(self, repo: str, block: RepoCoreBlock) -> None:
        self._execute(self._set_core_block_op(repo, block))

    async def aset_core_block(self, repo: str, block: RepoCoreBlock) -> None:
        await self._aexecute(self._set_core_block_op(repo, block))

    async def _set_core_block_op(self, repo: str, block: RepoCoreBlock) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await self._ensure_repo_row(conn, repo)
                await conn.execute(
                    """
                    INSERT INTO repo_core_blocks (
                        repo,
                        label,
                        description,
                        value,
                        token_budget,
                        read_only
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (repo, label) DO UPDATE
                    SET description = EXCLUDED.description,
                        value = EXCLUDED.value,
                        token_budget = EXCLUDED.token_budget,
                        read_only = EXCLUDED.read_only
                    """,
                    repo,
                    block.label,
                    block.description,
                    block.value,
                    block.token_budget,
                    block.read_only,
                )
        finally:
            await pool.release(conn)

    def list_core_blocks(self, repo: str) -> list[RepoCoreBlock]:
        return self._execute(self._list_core_blocks_op(repo))

    async def alist_core_blocks(self, repo: str) -> list[RepoCoreBlock]:
        return await self._aexecute(self._list_core_blocks_op(repo))

    async def _list_core_blocks_op(self, repo: str) -> list[RepoCoreBlock]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT label, description, value, token_budget, read_only
                FROM repo_core_blocks
                WHERE repo = $1
                ORDER BY label ASC
                """,
                repo,
            )
        finally:
            await pool.release(conn)
        return [_core_block_from_row(row) for row in rows]

    def upsert_claim(self, claim: MemoryClaim) -> MemoryClaim:
        async def _op() -> MemoryClaim:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, claim.repo)
                    await self._perform_upsert_claim(conn, claim)
                    row = await self._fetch_claim_row(conn, claim.repo, claim.claim_key)
            finally:
                await pool.release(conn)
            assert row is not None
            return _claim_from_row(row)

        return self._execute(_op())

    def get_claim_by_source_identity(
        self, repo: str, source_identity_key: str
    ) -> MemoryClaim | None:
        async def _op() -> MemoryClaim | None:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                row = await conn.fetchrow(
                    """
                    SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                           claim_kind, text, normalized_text, status, score, score_components,
                           first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                           embedding, embedding_provider, embedding_dimensions, embedding_version,
                           metadata
                    FROM memory_claims
                    WHERE repo = $1 AND source_identity_key = $2
                    """,
                    repo,
                    source_identity_key,
                )
                if row is None:
                    row = await conn.fetchrow(
                        """
                        SELECT mc.claim_id, mc.claim_key, mc.source_identity_key, mc.repo,
                               mc.scope_kind, mc.scope_ref, mc.claim_kind, mc.text,
                               mc.normalized_text, mc.status, mc.score, mc.score_components,
                               mc.first_seen_at, mc.last_seen_at, mc.last_revalidated_at,
                               mc.revalidation_mode, mc.embedding, mc.embedding_provider,
                               mc.embedding_dimensions, mc.embedding_version, mc.metadata
                        FROM memory_claims mc
                        JOIN claim_evidence ce
                          ON ce.repo = mc.repo
                         AND ce.claim_key = mc.claim_key
                        WHERE mc.repo = $1 AND ce.evidence_ref = $2
                        ORDER BY ce.observed_at DESC
                        LIMIT 1
                        """,
                        repo,
                        source_identity_key,
                    )
            finally:
                await pool.release(conn)
            return _claim_from_row(row) if row is not None else None

        return self._execute(_op())

    def list_claims(
        self,
        repo: str,
        statuses: set[ClaimStatus] | None = None,
    ) -> list[MemoryClaim]:
        return self._execute(self._list_claims_op(repo, statuses))

    async def alist_claims(
        self,
        repo: str,
        statuses: set[ClaimStatus] | None = None,
    ) -> list[MemoryClaim]:
        return await self._aexecute(self._list_claims_op(repo, statuses))

    async def _list_claims_op(
        self,
        repo: str,
        statuses: set[ClaimStatus] | None = None,
    ) -> list[MemoryClaim]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            if statuses:
                rows = await conn.fetch(
                    """
                    SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                           claim_kind, text, normalized_text, status, score, score_components,
                           first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                           embedding, embedding_provider, embedding_dimensions, embedding_version,
                           metadata
                    FROM memory_claims
                    WHERE repo = $1 AND status = ANY($2::text[])
                    ORDER BY score DESC, last_seen_at DESC, claim_key ASC
                    """,
                    repo,
                    [status.value for status in statuses],
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                           claim_kind, text, normalized_text, status, score, score_components,
                           first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                           embedding, embedding_provider, embedding_dimensions, embedding_version,
                           metadata
                    FROM memory_claims
                    WHERE repo = $1
                    ORDER BY score DESC, last_seen_at DESC, claim_key ASC
                    """,
                    repo,
                )
        finally:
            await pool.release(conn)
        return [_claim_from_row(row) for row in rows]

    def attach_claim_evidence(self, claim_key: str, evidence: ClaimEvidence) -> None:
        self._execute(self._attach_claim_evidence_op(claim_key, evidence))

    async def aattach_claim_evidence(self, claim_key: str, evidence: ClaimEvidence) -> None:
        await self._aexecute(self._attach_claim_evidence_op(claim_key, evidence))

    async def _attach_claim_evidence_op(self, claim_key: str, evidence: ClaimEvidence) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO claim_evidence (
                        evidence_id,
                        repo,
                        claim_key,
                        run_id,
                        evidence_kind,
                        evidence_ref,
                        evidence_text,
                        weight,
                        observed_at,
                        source_thread_id,
                        source_path,
                        source_entity_id,
                        metadata
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        $9, $10, $11, $12, $13::jsonb
                    )
                    ON CONFLICT (repo, evidence_id) DO NOTHING
                    """,
                    evidence.evidence_id,
                    evidence.repo,
                    claim_key,
                    evidence.run_id,
                    evidence.evidence_kind,
                    evidence.evidence_ref,
                    evidence.evidence_text,
                    evidence.weight,
                    evidence.observed_at,
                    evidence.source_thread_id,
                    evidence.source_path,
                    evidence.source_entity_id,
                    json.dumps(evidence.metadata),
                )
        finally:
            await pool.release(conn)

    def list_claim_evidence(self, repo: str, claim_key: str) -> list[ClaimEvidence]:
        return self._execute(self._list_claim_evidence_op(repo, claim_key))

    async def alist_claim_evidence(self, repo: str, claim_key: str) -> list[ClaimEvidence]:
        return await self._aexecute(self._list_claim_evidence_op(repo, claim_key))

    async def _list_claim_evidence_op(self, repo: str, claim_key: str) -> list[ClaimEvidence]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT evidence_id, repo, claim_key, run_id, evidence_kind, evidence_ref,
                       evidence_text, weight, observed_at, source_thread_id,
                       source_path, source_entity_id, metadata
                FROM claim_evidence
                WHERE repo = $1 AND claim_key = $2
                ORDER BY observed_at ASC, evidence_id ASC
                """,
                repo,
                claim_key,
            )
        finally:
            await pool.release(conn)
        return [_claim_evidence_from_row(row) for row in rows]

    def find_related_claims(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        claim_kind: ClaimKind | None = None,
        scope_kind: ClaimScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int = 5,
    ) -> list[tuple[MemoryClaim, float]]:
        if not query_embedding or not any(query_embedding):
            return []
        return self._execute(
            self._find_related_claims_op(
                repo,
                query_embedding,
                claim_kind=claim_kind,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                limit=limit,
            )
        )

    async def afind_related_claims(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        claim_kind: ClaimKind | None = None,
        scope_kind: ClaimScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int = 5,
    ) -> list[tuple[MemoryClaim, float]]:
        if not query_embedding or not any(query_embedding):
            return []
        return await self._aexecute(
            self._find_related_claims_op(
                repo,
                query_embedding,
                claim_kind=claim_kind,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                limit=limit,
            )
        )

    async def _find_related_claims_op(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        claim_kind: ClaimKind | None = None,
        scope_kind: ClaimScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int = 5,
    ) -> list[tuple[MemoryClaim, float]]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                       claim_kind, text, normalized_text, status, score, score_components,
                       first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                       embedding, embedding_provider, embedding_dimensions, embedding_version,
                       metadata,
                       1 - (embedding <=> $2::vector) AS similarity
                FROM memory_claims
                WHERE repo = $1
                  AND embedding IS NOT NULL
                  AND ($3::text IS NULL OR claim_kind = $3)
                  AND ($4::text IS NULL OR scope_kind = $4)
                  AND ($5::text IS NULL OR scope_ref = $5)
                ORDER BY embedding <=> $2::vector ASC, score DESC, claim_key ASC
                LIMIT $6
                """,
                repo,
                _vector_literal(query_embedding),
                claim_kind.value if claim_kind else None,
                scope_kind.value if scope_kind else None,
                scope_ref,
                limit,
            )
        finally:
            await pool.release(conn)
        return [(_claim_from_row(row), float(row["similarity"])) for row in rows]

    def create_repo_core_snapshot(self, snapshot: RepoCoreSnapshot) -> None:
        async def _op() -> None:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, snapshot.repo)
                    await conn.execute(
                        """
                        INSERT INTO repo_core_snapshots (
                            snapshot_id,
                            repo,
                            compiled_at,
                            source_watermark,
                            blocks,
                            source_claim_keys,
                            metadata
                        )
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)
                        ON CONFLICT (snapshot_id) DO UPDATE
                        SET repo = EXCLUDED.repo,
                            compiled_at = EXCLUDED.compiled_at,
                            source_watermark = EXCLUDED.source_watermark,
                            blocks = EXCLUDED.blocks,
                            source_claim_keys = EXCLUDED.source_claim_keys,
                            metadata = EXCLUDED.metadata
                        """,
                        snapshot.snapshot_id,
                        snapshot.repo,
                        snapshot.compiled_at,
                        snapshot.source_watermark,
                        json.dumps(
                            [
                                {
                                    "label": block.label,
                                    "description": block.description,
                                    "value": block.value,
                                    "token_budget": block.token_budget,
                                    "read_only": block.read_only,
                                }
                                for block in snapshot.blocks
                            ]
                        ),
                        json.dumps(snapshot.source_claim_keys),
                        json.dumps(snapshot.metadata),
                    )
            finally:
                await pool.release(conn)

        self._execute(_op())

    def get_latest_repo_core_snapshot(self, repo: str) -> RepoCoreSnapshot | None:
        return self._execute(self._get_latest_repo_core_snapshot_op(repo))

    async def aget_latest_repo_core_snapshot(self, repo: str) -> RepoCoreSnapshot | None:
        return await self._aexecute(self._get_latest_repo_core_snapshot_op(repo))

    async def _get_latest_repo_core_snapshot_op(self, repo: str) -> RepoCoreSnapshot | None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            row = await conn.fetchrow(
                """
                SELECT snapshot_id, repo, compiled_at, source_watermark,
                       blocks, source_claim_keys, metadata
                FROM repo_core_snapshots
                WHERE repo = $1
                ORDER BY compiled_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                repo,
            )
        finally:
            await pool.release(conn)
        return _snapshot_from_row(row) if row is not None else None

    def create_dream_run(self, run: DreamRun) -> None:
        async def _op() -> None:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, run.repo)
                    await conn.execute(
                        """
                        INSERT INTO dream_runs (
                            run_id, repo, run_kind, status, started_at, finished_at,
                            worker_id, cursor_before, cursor_after, signal_count,
                            claim_candidate_count, merged_count, promoted_count,
                            snapshot_id, summary
                        )
                        VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15::jsonb
                        )
                        ON CONFLICT (run_id) DO UPDATE
                        SET repo = EXCLUDED.repo,
                            run_kind = EXCLUDED.run_kind,
                            status = EXCLUDED.status,
                            started_at = EXCLUDED.started_at,
                            finished_at = EXCLUDED.finished_at,
                            worker_id = EXCLUDED.worker_id,
                            cursor_before = EXCLUDED.cursor_before,
                            cursor_after = EXCLUDED.cursor_after,
                            signal_count = EXCLUDED.signal_count,
                            claim_candidate_count = EXCLUDED.claim_candidate_count,
                            merged_count = EXCLUDED.merged_count,
                            promoted_count = EXCLUDED.promoted_count,
                            snapshot_id = EXCLUDED.snapshot_id,
                            summary = EXCLUDED.summary
                        """,
                        run.run_id,
                        run.repo,
                        run.run_kind,
                        run.status,
                        run.started_at,
                        run.finished_at,
                        run.worker_id,
                        run.cursor_before,
                        run.cursor_after,
                        run.signal_count,
                        run.claim_candidate_count,
                        run.merged_count,
                        run.promoted_count,
                        run.snapshot_id,
                        json.dumps(run.summary),
                    )
            finally:
                await pool.release(conn)

        self._execute(_op())

    def finalize_dream_run(self, run: DreamRun) -> None:
        self.create_dream_run(run)

    def list_dream_runs(self, repo: str) -> list[DreamRun]:
        async def _op() -> list[DreamRun]:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                rows = await conn.fetch(
                    """
                    SELECT run_id, repo, run_kind, status, started_at, finished_at,
                           worker_id, cursor_before, cursor_after, signal_count,
                           claim_candidate_count, merged_count, promoted_count,
                           snapshot_id, summary
                    FROM dream_runs
                    WHERE repo = $1
                    ORDER BY started_at ASC, run_id ASC
                    """,
                    repo,
                )
            finally:
                await pool.release(conn)
            return [_dream_run_from_row(row) for row in rows]

        return self._execute(_op())

    def acquire_dreaming_lease(
        self,
        repo: str,
        worker_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> bool:
        async def _op() -> bool:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                # Two writers racing on the very first lease (no row exists
                # yet) would both see empty ``SELECT … FOR UPDATE`` results
                # and both run the INSERT-on-conflict — the second silently
                # overwrites the first's lease. Hold an advisory lock keyed
                # on ``("dreaming-lease", repo)`` for the whole transaction
                # so only one writer can be in the check-and-set window at
                # a time.
                lock_high, lock_low = _advisory_lock_keys("dreaming-lease", repo)
                async with conn.transaction():
                    await self._ensure_repo_row(conn, repo)
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock($1::int, $2::int)",
                        lock_high,
                        lock_low,
                    )
                    expires_at = now if ttl_seconds <= 0 else now + timedelta(seconds=ttl_seconds)
                    row = await conn.fetchrow(
                        """
                        SELECT worker_id, expires_at
                        FROM dreaming_leases
                        WHERE repo = $1
                        FOR UPDATE
                        """,
                        repo,
                    )
                    if row is not None:
                        current_expires = row["expires_at"]
                        if current_expires.tzinfo is None:
                            current_expires = current_expires.replace(tzinfo=UTC)
                        if row["worker_id"] != worker_id and current_expires > now:
                            return False
                    await conn.execute(
                        """
                        INSERT INTO dreaming_leases (repo, worker_id, expires_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (repo) DO UPDATE
                        SET worker_id = EXCLUDED.worker_id,
                            expires_at = EXCLUDED.expires_at
                        """,
                        repo,
                        worker_id,
                        expires_at,
                    )
                    return True
            finally:
                await pool.release(conn)

        return self._execute(_op())

    def release_dreaming_lease(self, repo: str, worker_id: str) -> None:
        async def _op() -> None:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                await conn.execute(
                    """
                    DELETE FROM dreaming_leases
                    WHERE repo = $1 AND worker_id = $2
                    """,
                    repo,
                    worker_id,
                )
            finally:
                await pool.release(conn)

        self._execute(_op())

    def get_dreaming_cursor(self, repo: str) -> int:
        async def _op() -> int:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                row = await conn.fetchrow(
                    "SELECT dreaming_cursor FROM sync_state WHERE repo = $1",
                    repo,
                )
            finally:
                await pool.release(conn)
            return int(row["dreaming_cursor"]) if row is not None else 0

        return self._execute(_op())

    def set_dreaming_cursor(self, repo: str, watermark: int) -> None:
        async def _op() -> None:
            pool = self._pool()
            conn = await pool.acquire()
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, repo)
                    await conn.execute(
                        """
                        INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq, dreaming_cursor)
                        VALUES ($1, 0, 0, $2)
                        ON CONFLICT (repo) DO UPDATE
                        SET dreaming_cursor = GREATEST(sync_state.dreaming_cursor, EXCLUDED.dreaming_cursor)
                        """,
                        repo,
                        watermark,
                    )
            finally:
                await pool.release(conn)

        self._execute(_op())

    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        self._execute(self._set_last_compiled_seq_op(repo, observed_seq))

    async def aset_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        await self._aexecute(self._set_last_compiled_seq_op(repo, observed_seq))

    async def _set_last_compiled_seq_op(self, repo: str, observed_seq: int) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await self._ensure_repo_row(conn, repo)
                await conn.execute(
                    """
                    INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq, dreaming_cursor)
                    VALUES ($1, 0, $2, 0)
                    ON CONFLICT (repo) DO UPDATE
                    SET last_compiled_seq = GREATEST(
                        sync_state.last_compiled_seq,
                        EXCLUDED.last_compiled_seq
                    )
                    """,
                    repo,
                    observed_seq,
                )
        finally:
            await pool.release(conn)

    def get_sync_state(self, repo: str) -> dict[str, int]:
        return self._execute(self._get_sync_state_op(repo))

    async def aget_sync_state(self, repo: str) -> dict[str, int]:
        return await self._aexecute(self._get_sync_state_op(repo))

    async def _get_sync_state_op(self, repo: str) -> dict[str, int]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            row = await conn.fetchrow(
                """
                SELECT last_observed_seq, last_compiled_seq, dreaming_cursor
                FROM sync_state
                WHERE repo = $1
                """,
                repo,
            )
        finally:
            await pool.release(conn)
        if row is None:
            return {"last_observed_seq": 0, "last_compiled_seq": 0, "dreaming_cursor": 0}
        return {
            "last_observed_seq": row["last_observed_seq"],
            "last_compiled_seq": row["last_compiled_seq"],
            "dreaming_cursor": row["dreaming_cursor"],
        }

    def record_lineage(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        self._execute(self._record_lineage_op(entity_id, predecessor_id, reason, confidence))

    async def arecord_lineage(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        await self._aexecute(self._record_lineage_op(entity_id, predecessor_id, reason, confidence))

    async def _record_lineage_op(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO entity_links (
                        entity_id,
                        related_entity_id,
                        link_type,
                        reason,
                        confidence
                    )
                    VALUES ($1, $2, 'predecessor', $3, $4)
                    ON CONFLICT (entity_id, related_entity_id, link_type) DO UPDATE
                    SET reason = EXCLUDED.reason,
                        confidence = EXCLUDED.confidence
                    """,
                    entity_id,
                    predecessor_id,
                    reason,
                    confidence,
                )
                # Lock the row before reading predecessor_ids so concurrent
                # writers serialize on this entity instead of clobbering
                # each other's appends.
                row = await conn.fetchrow(
                    "SELECT predecessor_ids FROM entities WHERE entity_id = $1 FOR UPDATE",
                    entity_id,
                )
                predecessor_ids_value = row["predecessor_ids"] if row else []
                if isinstance(predecessor_ids_value, str):
                    predecessor_ids_value = json.loads(predecessor_ids_value)
                predecessor_ids = list(predecessor_ids_value or [])
                if predecessor_id not in predecessor_ids:
                    predecessor_ids.append(predecessor_id)
                    await conn.execute(
                        """
                        UPDATE entities
                        SET predecessor_ids = $2::jsonb
                        WHERE entity_id = $1
                        """,
                        entity_id,
                        json.dumps(predecessor_ids),
                    )
        finally:
            await pool.release(conn)

    def list_lineage(self) -> list[dict[str, Any]]:
        return self._execute(self._list_lineage_op())

    async def alist_lineage(self) -> list[dict[str, Any]]:
        return await self._aexecute(self._list_lineage_op())

    async def _list_lineage_op(self) -> list[dict[str, Any]]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT entity_id, related_entity_id, reason, confidence
                FROM entity_links
                WHERE link_type = 'predecessor'
                ORDER BY entity_id ASC, related_entity_id ASC
                """
            )
        finally:
            await pool.release(conn)
        return [
            {
                "entity_id": row["entity_id"],
                "predecessor_id": row["related_entity_id"],
                "reason": row["reason"],
                "confidence": row["confidence"],
            }
            for row in rows
        ]

    def search_vector_entities(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        current_path: str | None = None,
        current_entity_id: str | None = None,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        if not query_embedding or not any(query_embedding):
            return []
        return self._execute(
            self._search_vector_entities_op(
                repo,
                query_embedding,
                current_path=current_path,
                current_entity_id=current_entity_id,
                limit=limit,
            )
        )

    async def asearch_vector_entities(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        current_path: str | None = None,
        current_entity_id: str | None = None,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        if not query_embedding or not any(query_embedding):
            return []
        return await self._aexecute(
            self._search_vector_entities_op(
                repo,
                query_embedding,
                current_path=current_path,
                current_entity_id=current_entity_id,
                limit=limit,
            )
        )

    async def _search_vector_entities_op(
        self,
        repo: str,
        query_embedding: Sequence[float],
        *,
        current_path: str | None = None,
        current_entity_id: str | None = None,
        limit: int = 5,
    ) -> list[VectorSearchHit]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT er.entity_id, er.repo, er.path, er.language, er.kind, er.name,
                       er.qualified_name, er.observed_seq, er.signature,
                       er.parent_qualified_name, er.docstring, er.comment,
                       er.body, er.retrieval_text, er.start_line, er.end_line,
                       1 - (er.embedding <=> $2::vector) AS similarity
                FROM entity_revisions er
                JOIN entities e
                  ON e.entity_id = er.entity_id
                 AND e.current_observed_seq = er.observed_seq
                WHERE e.repo = $1
                  AND er.embedding IS NOT NULL
                  AND ($3::text IS NULL OR er.path <> $3)
                  AND ($4::text IS NULL OR er.entity_id <> $4)
                ORDER BY er.embedding <=> $2::vector ASC,
                         er.observed_seq DESC,
                         er.qualified_name ASC
                LIMIT $5
                """,
                repo,
                _vector_literal(query_embedding),
                current_path,
                current_entity_id,
                limit,
            )
        finally:
            await pool.release(conn)
        return [
            VectorSearchHit(
                entity=_entity_revision_from_row(row),
                similarity=float(row["similarity"]),
            )
            for row in rows
        ]

    def _pool(self) -> asyncpg.Pool:
        return get_pool(self.database_url)

    def _execute(self, coro: Coroutine[Any, Any, T]) -> T:
        self._ensure_schema()
        return run_async(coro)

    async def _aexecute(self, coro: Coroutine[Any, Any, T]) -> T:
        self._ensure_schema()
        return await arun(coro)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with _SCHEMA_READY_LOCK:
            if self.database_url in _SCHEMA_READY_BY_URL:
                self._schema_ready = True
                # Pre-warm the pool from a thread that is *not* the
                # dedicated asyncpg loop, even on cache hit. ``_op()``
                # methods that run on the loop call ``_pool()`` which on a
                # cache miss would re-submit pool creation to the same
                # loop and deadlock.
                get_pool(self.database_url)
                return
            # Same pre-warm in the cold path: the validation call below
            # runs against its own connection, so the on-loop ``_op()``
            # callers that follow must observe an already-cached pool.
            get_pool(self.database_url)
            run_async(
                validate_repo_memory_schema_async(
                    self.database_url,
                    vector_dimensions=self.embedding_provider.dimensions,
                )
            )
            _SCHEMA_READY_BY_URL.add(self.database_url)
            self._schema_ready = True

    async def _ensure_repo_row(self, conn: Any, repo: str) -> None:
        await conn.execute(
            "INSERT INTO repositories (repo) VALUES ($1) ON CONFLICT (repo) DO NOTHING",
            repo,
        )
        await conn.execute(
            """
            INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq, dreaming_cursor)
            VALUES ($1, 0, 0, 0)
            ON CONFLICT (repo) DO NOTHING
            """,
            repo,
        )

    async def _bump_last_observed_seq(self, conn: Any, repo: str, observed_seq: int) -> None:
        await conn.execute(
            """
            INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq, dreaming_cursor)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (repo) DO UPDATE
            SET last_observed_seq = GREATEST(
                sync_state.last_observed_seq,
                EXCLUDED.last_observed_seq
            )
            """,
            repo,
            observed_seq,
        )

    async def _perform_upsert_claim(self, conn: Any, claim: MemoryClaim) -> None:
        await conn.execute(
            """
            INSERT INTO memory_claims (
                claim_id,
                claim_key,
                source_identity_key,
                repo,
                scope_kind,
                scope_ref,
                claim_kind,
                text,
                normalized_text,
                status,
                score,
                score_components,
                first_seen_at,
                last_seen_at,
                last_revalidated_at,
                revalidation_mode,
                embedding,
                embedding_provider,
                embedding_dimensions,
                embedding_version,
                metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12::jsonb, $13, $14, $15, $16, $17::vector,
                $18, $19, $20, $21::jsonb
            )
            ON CONFLICT (repo, claim_key) DO UPDATE
            SET source_identity_key = EXCLUDED.source_identity_key,
                scope_kind = EXCLUDED.scope_kind,
                scope_ref = EXCLUDED.scope_ref,
                claim_kind = EXCLUDED.claim_kind,
                text = EXCLUDED.text,
                normalized_text = EXCLUDED.normalized_text,
                status = EXCLUDED.status,
                score = EXCLUDED.score,
                score_components = EXCLUDED.score_components,
                first_seen_at = LEAST(memory_claims.first_seen_at, EXCLUDED.first_seen_at),
                last_seen_at = GREATEST(memory_claims.last_seen_at, EXCLUDED.last_seen_at),
                last_revalidated_at = EXCLUDED.last_revalidated_at,
                revalidation_mode = EXCLUDED.revalidation_mode,
                embedding = EXCLUDED.embedding,
                embedding_provider = EXCLUDED.embedding_provider,
                embedding_dimensions = EXCLUDED.embedding_dimensions,
                embedding_version = EXCLUDED.embedding_version,
                metadata = EXCLUDED.metadata
            """,
            claim.claim_id,
            claim.claim_key,
            claim.source_identity_key,
            claim.repo,
            claim.scope_kind.value,
            claim.scope_ref,
            claim.claim_kind.value,
            claim.text,
            claim.normalized_text,
            claim.status.value,
            claim.score,
            json.dumps(claim.score_components),
            claim.first_seen_at,
            claim.last_seen_at,
            claim.last_revalidated_at,
            claim.revalidation_mode.value,
            _vector_literal(claim.embedding),
            claim.embedding_provider,
            claim.embedding_dimensions,
            claim.embedding_version,
            json.dumps(claim.metadata),
        )

    async def _fetch_claim_row(self, conn: Any, repo: str, claim_key: str) -> asyncpg.Record | None:
        return await conn.fetchrow(
            """
            SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                   claim_kind, text, normalized_text, status, score, score_components,
                   first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                   embedding, embedding_provider, embedding_dimensions, embedding_version,
                   metadata
            FROM memory_claims
            WHERE repo = $1 AND claim_key = $2
            """,
            repo,
            claim_key,
        )

    # ------------------------------------------------------------------
    # Atomic helpers (concurrency / parallel agents)
    # ------------------------------------------------------------------

    def allocate_observed_seq(self, repo: str) -> int:
        """Atomically reserve the next ``observed_seq`` for ``repo``.

        Replaces the racy ``last_observed_seq + 1`` read-then-write so 100
        parallel agents on the same repo each get a distinct, monotonically
        increasing seq.
        """
        return self._execute(self._allocate_observed_seq_op(repo))

    async def aallocate_observed_seq(self, repo: str) -> int:
        return await self._aexecute(self._allocate_observed_seq_op(repo))

    async def _allocate_observed_seq_op(self, repo: str) -> int:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            async with conn.transaction():
                await self._ensure_repo_row(conn, repo)
                row = await conn.fetchrow(
                    """
                    UPDATE sync_state
                    SET last_observed_seq = last_observed_seq + 1
                    WHERE repo = $1
                    RETURNING last_observed_seq
                    """,
                    repo,
                )
        finally:
            await pool.release(conn)
        assert row is not None
        return int(row["last_observed_seq"])

    def iter_entities_for_path(self, repo: str, path: str) -> list[EntityRevision]:
        """Per-path entity index — avoids the full ``entities`` scan that the
        original ``iter_entities(repo)`` did per changed path during flush.
        """
        return self._execute(self._iter_entities_for_path_op(repo, path))

    async def aiter_entities_for_path(self, repo: str, path: str) -> list[EntityRevision]:
        return await self._aexecute(self._iter_entities_for_path_op(repo, path))

    async def _iter_entities_for_path_op(self, repo: str, path: str) -> list[EntityRevision]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT er.entity_id, er.repo, er.path, er.language, er.kind, er.name,
                       er.qualified_name, er.observed_seq, er.signature,
                       er.parent_qualified_name, er.docstring, er.comment,
                       er.body, er.retrieval_text, er.start_line, er.end_line
                FROM entity_revisions er
                JOIN entities e
                  ON e.entity_id = er.entity_id
                 AND e.current_observed_seq = er.observed_seq
                WHERE e.repo = $1 AND e.path = $2
                ORDER BY er.observed_seq DESC, er.qualified_name ASC
                """,
                repo,
                path,
            )
        finally:
            await pool.release(conn)
        return [_entity_revision_from_row(row) for row in rows]

    def upsert_candidate_with_dedup(
        self,
        candidate: MemoryClaim,
        *,
        merge_similarity_threshold: float,
        jaccard_threshold: float,
    ) -> tuple[MemoryClaim, bool, bool]:
        """Light-phase dedup as one ACID step.

        Two writers on the same ``(repo, source_identity_key)`` collide on a
        ``pg_advisory_xact_lock`` so only one of them runs the dedup→insert
        path; the other observes the freshly inserted row via
        ``SELECT … FOR UPDATE`` and merges into it.

        Returns ``(stored_claim, vector_merged, jaccard_merged)`` mirroring the
        in-memory ``_upsert_candidate_with_dedup`` shape.
        """
        return self._execute(
            self._upsert_candidate_with_dedup_op(
                candidate,
                merge_similarity_threshold=merge_similarity_threshold,
                jaccard_threshold=jaccard_threshold,
            )
        )

    async def aupsert_candidate_with_dedup(
        self,
        candidate: MemoryClaim,
        *,
        merge_similarity_threshold: float,
        jaccard_threshold: float,
    ) -> tuple[MemoryClaim, bool, bool]:
        return await self._aexecute(
            self._upsert_candidate_with_dedup_op(
                candidate,
                merge_similarity_threshold=merge_similarity_threshold,
                jaccard_threshold=jaccard_threshold,
            )
        )

    async def _upsert_candidate_with_dedup_op(
        self,
        candidate: MemoryClaim,
        *,
        merge_similarity_threshold: float,
        jaccard_threshold: float,
    ) -> tuple[MemoryClaim, bool, bool]:
        pool = self._pool()
        conn = await pool.acquire()
        try:
            lock_high, lock_low = _advisory_lock_keys(
                candidate.repo,
                candidate.source_identity_key or candidate.claim_key,
            )
            async with conn.transaction():
                await self._ensure_repo_row(conn, candidate.repo)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1::int, $2::int)",
                    lock_high,
                    lock_low,
                )
                existing_row = await conn.fetchrow(
                    """
                    SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                           claim_kind, text, normalized_text, status, score, score_components,
                           first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                           embedding, embedding_provider, embedding_dimensions, embedding_version,
                           metadata
                    FROM memory_claims
                    WHERE repo = $1 AND source_identity_key = $2
                    FOR UPDATE
                    """,
                    candidate.repo,
                    candidate.source_identity_key,
                )
                if existing_row is not None:
                    existing = _claim_from_row(existing_row)
                    merged = _merge_claim_objects(existing, candidate)
                    await self._perform_upsert_claim(conn, merged)
                    stored_row = await self._fetch_claim_row(conn, candidate.repo, merged.claim_key)
                    assert stored_row is not None
                    return _claim_from_row(stored_row), False, False

                if candidate.embedding and any(candidate.embedding):
                    vector_row = await conn.fetchrow(
                        """
                        SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                               claim_kind, text, normalized_text, status, score, score_components,
                               first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                               embedding, embedding_provider, embedding_dimensions, embedding_version,
                               metadata,
                               1 - (embedding <=> $2::vector) AS similarity
                        FROM memory_claims
                        WHERE repo = $1
                          AND embedding IS NOT NULL
                          AND claim_kind = $3
                          AND scope_kind = $4
                          AND scope_ref = $5
                        ORDER BY embedding <=> $2::vector ASC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        candidate.repo,
                        _vector_literal(candidate.embedding),
                        candidate.claim_kind.value,
                        candidate.scope_kind.value,
                        candidate.scope_ref,
                    )
                    if (
                        vector_row is not None
                        and float(vector_row["similarity"]) >= merge_similarity_threshold
                    ):
                        target = _claim_from_row(vector_row)
                        merged = _merge_with_source(target, candidate)
                        await self._perform_upsert_claim(conn, merged)
                        stored_row = await self._fetch_claim_row(
                            conn, candidate.repo, merged.claim_key
                        )
                        assert stored_row is not None
                        return _claim_from_row(stored_row), True, False

                # Jaccard dedup is best-effort — leftovers are collapsed by
                # a follow-up dream pass via the standard merge path.
                # Crucially we do NOT take ``FOR UPDATE`` here: that would
                # serialize every writer with the same (claim_kind,
                # scope_kind, scope_ref) regardless of whether their source
                # identities collide, killing parallelism for unrelated
                # work. The advisory lock above plus the source-identity
                # ``FOR UPDATE`` already cover duplicate-by-source safety.
                rows = await conn.fetch(
                    """
                    SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                           claim_kind, text, normalized_text, status, score, score_components,
                           first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                           embedding, embedding_provider, embedding_dimensions, embedding_version,
                           metadata
                    FROM memory_claims
                    WHERE repo = $1
                      AND claim_kind = $2
                      AND scope_kind = $3
                      AND scope_ref = $4
                    """,
                    candidate.repo,
                    candidate.claim_kind.value,
                    candidate.scope_kind.value,
                    candidate.scope_ref,
                )
                jaccard_target = _find_jaccard_target(rows, candidate, threshold=jaccard_threshold)
                if jaccard_target is not None:
                    merged = _merge_with_source(jaccard_target, candidate)
                    await self._perform_upsert_claim(conn, merged)
                    stored_row = await self._fetch_claim_row(conn, candidate.repo, merged.claim_key)
                    assert stored_row is not None
                    return _claim_from_row(stored_row), False, True

                await self._perform_upsert_claim(conn, candidate)
                stored_row = await self._fetch_claim_row(conn, candidate.repo, candidate.claim_key)
                assert stored_row is not None
                return _claim_from_row(stored_row), False, False
        finally:
            await pool.release(conn)
