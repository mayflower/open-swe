from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
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
from .models import MemoryMetadata, build_metadata

T = TypeVar("T")


@dataclass(slots=True)
class VectorSearchHit:
    entity: EntityRevision
    similarity: float


def _run_async_blocking(coro: Awaitable[T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    payload: dict[str, T] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            payload["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - exercised via caller paths
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return payload["value"]


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


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
    embedding = row["embedding"] or []
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
        embedding=list(embedding),
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
        self._ensure_schema()

        async def _op() -> list[str]:
            conn = await asyncpg.connect(self.database_url)
            try:
                rows = await conn.fetch(
                    """
                    SELECT repo
                    FROM repositories
                    ORDER BY repo ASC
                    """
                )
            finally:
                await conn.close()
            return [str(row["repo"]) for row in rows]

        return self._execute(_op())

    def upsert_file_revision(self, revision: FileRevision) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def get_file(self, repo: str, path: str) -> RepoFile | None:
        async def _op() -> RepoFile | None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
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

        return self._execute(_op())

    def upsert_entity_revision(self, revision: EntityRevision) -> None:
        embedding = self.embedding_provider.embed(revision.retrieval_text)

        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, revision.repo)
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
                    await self._bump_last_observed_seq(conn, revision.repo, revision.observed_seq)
            finally:
                await conn.close()

        self._execute(_op())

    def get_entity(self, entity_id: str) -> CodeEntity | None:
        async def _op() -> CodeEntity | None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
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

        return self._execute(_op())

    def iter_entities(self, repo: str) -> list[EntityRevision]:
        async def _op() -> list[EntityRevision]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [_entity_revision_from_row(row) for row in rows]

        return self._execute(_op())

    def append_repo_event(self, event: RepoEvent) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def list_repo_events(self, repo: str) -> list[RepoEvent]:
        async def _op() -> list[RepoEvent]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [_repo_event_from_row(row) for row in rows]

        return self._execute(_op())

    def set_core_block(self, repo: str, block: RepoCoreBlock) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def list_core_blocks(self, repo: str) -> list[RepoCoreBlock]:
        async def _op() -> list[RepoCoreBlock]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [_core_block_from_row(row) for row in rows]

        return self._execute(_op())

    def upsert_claim(self, claim: MemoryClaim) -> MemoryClaim:
        async def _op() -> MemoryClaim:
            conn = await asyncpg.connect(self.database_url)
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, claim.repo)
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
                    row = await conn.fetchrow(
                        """
                        SELECT claim_id, claim_key, source_identity_key, repo, scope_kind, scope_ref,
                               claim_kind, text, normalized_text, status, score, score_components,
                               first_seen_at, last_seen_at, last_revalidated_at, revalidation_mode,
                               embedding, embedding_provider, embedding_dimensions, embedding_version,
                               metadata
                        FROM memory_claims
                        WHERE repo = $1 AND claim_key = $2
                        """,
                        claim.repo,
                        claim.claim_key,
                    )
            finally:
                await conn.close()
            assert row is not None
            return _claim_from_row(row)

        return self._execute(_op())

    def get_claim_by_source_identity(self, repo: str, source_identity_key: str) -> MemoryClaim | None:
        async def _op() -> MemoryClaim | None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return _claim_from_row(row) if row is not None else None

        return self._execute(_op())

    def list_claims(
        self,
        repo: str,
        statuses: set[ClaimStatus] | None = None,
    ) -> list[MemoryClaim]:
        async def _op() -> list[MemoryClaim]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [_claim_from_row(row) for row in rows]

        return self._execute(_op())

    def attach_claim_evidence(self, claim_key: str, evidence: ClaimEvidence) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def list_claim_evidence(self, repo: str, claim_key: str) -> list[ClaimEvidence]:
        async def _op() -> list[ClaimEvidence]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [_claim_evidence_from_row(row) for row in rows]

        return self._execute(_op())

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

        async def _op() -> list[tuple[MemoryClaim, float]]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [
                (_claim_from_row(row), float(row["similarity"]))
                for row in rows
            ]

        return self._execute(_op())

    def create_repo_core_snapshot(self, snapshot: RepoCoreSnapshot) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def get_latest_repo_core_snapshot(self, repo: str) -> RepoCoreSnapshot | None:
        async def _op() -> RepoCoreSnapshot | None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return _snapshot_from_row(row) if row is not None else None

        return self._execute(_op())

    def create_dream_run(self, run: DreamRun) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def finalize_dream_run(self, run: DreamRun) -> None:
        self.create_dream_run(run)

    def list_dream_runs(self, repo: str) -> list[DreamRun]:
        async def _op() -> list[DreamRun]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
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
            conn = await asyncpg.connect(self.database_url)
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, repo)
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
                await conn.close()

        return self._execute(_op())

    def release_dreaming_lease(self, repo: str, worker_id: str) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def get_dreaming_cursor(self, repo: str) -> int:
        async def _op() -> int:
            conn = await asyncpg.connect(self.database_url)
            try:
                row = await conn.fetchrow(
                    "SELECT dreaming_cursor FROM sync_state WHERE repo = $1",
                    repo,
                )
            finally:
                await conn.close()
            return int(row["dreaming_cursor"]) if row is not None else 0

        return self._execute(_op())

    def set_dreaming_cursor(self, repo: str, watermark: int) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()

        self._execute(_op())

    def get_sync_state(self, repo: str) -> dict[str, int]:
        async def _op() -> dict[str, int]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            if row is None:
                return {"last_observed_seq": 0, "last_compiled_seq": 0, "dreaming_cursor": 0}
            return {
                "last_observed_seq": row["last_observed_seq"],
                "last_compiled_seq": row["last_compiled_seq"],
                "dreaming_cursor": row["dreaming_cursor"],
            }

        return self._execute(_op())

    def record_lineage(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
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
                    row = await conn.fetchrow(
                        "SELECT predecessor_ids FROM entities WHERE entity_id = $1",
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
                await conn.close()

        self._execute(_op())

    def list_lineage(self) -> list[dict[str, Any]]:
        async def _op() -> list[dict[str, Any]]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [
                {
                    "entity_id": row["entity_id"],
                    "predecessor_id": row["related_entity_id"],
                    "reason": row["reason"],
                    "confidence": row["confidence"],
                }
                for row in rows
            ]

        return self._execute(_op())

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

        async def _op() -> list[VectorSearchHit]:
            conn = await asyncpg.connect(self.database_url)
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
                await conn.close()
            return [
                VectorSearchHit(
                    entity=_entity_revision_from_row(row),
                    similarity=float(row["similarity"]),
                )
                for row in rows
            ]

        return self._execute(_op())

    def _execute(self, coro: Awaitable[T]) -> T:
        self._ensure_schema()
        return _run_async_blocking(coro)

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            _run_async_blocking(self._ensure_schema_async())
            self._schema_ready = True

    async def _ensure_schema_async(self) -> None:
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            statements = [
                """
                CREATE TABLE IF NOT EXISTS repositories (
                    repo TEXT PRIMARY KEY
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS files (
                    repo TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    current_observed_seq INTEGER NOT NULL,
                    PRIMARY KEY (repo, path)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS file_revisions (
                    repo TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    observed_seq INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (repo, path, observed_seq)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    current_observed_seq INTEGER NOT NULL,
                    predecessor_ids JSONB NOT NULL DEFAULT '[]'::jsonb
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS entity_revisions (
                    entity_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    observed_seq INTEGER NOT NULL,
                    signature TEXT NOT NULL DEFAULT '',
                    parent_qualified_name TEXT NULL,
                    docstring TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    retrieval_text TEXT NOT NULL DEFAULT '',
                    start_line INTEGER NULL,
                    end_line INTEGER NULL,
                    embedding VECTOR({self.embedding_provider.dimensions}) NULL,
                    PRIMARY KEY (entity_id, observed_seq)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS entity_links (
                    entity_id TEXT NOT NULL,
                    related_entity_id TEXT NOT NULL,
                    link_type TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                    PRIMARY KEY (entity_id, related_entity_id, link_type)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS repo_events (
                    event_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    observed_seq INTEGER NOT NULL,
                    path TEXT NULL,
                    entity_id TEXT NULL,
                    evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS repo_core_blocks (
                    repo TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    value TEXT NOT NULL,
                    token_budget INTEGER NOT NULL,
                    read_only BOOLEAN NOT NULL DEFAULT TRUE,
                    PRIMARY KEY (repo, label)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    repo TEXT PRIMARY KEY,
                    last_observed_seq INTEGER NOT NULL DEFAULT 0,
                    last_compiled_seq INTEGER NOT NULL DEFAULT 0,
                    dreaming_cursor INTEGER NOT NULL DEFAULT 0
                )
                """,
                f"""
                CREATE TABLE IF NOT EXISTS memory_claims (
                    claim_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    source_identity_key TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_ref TEXT NOT NULL,
                    claim_kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score DOUBLE PRECISION NOT NULL DEFAULT 0,
                    score_components JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    last_revalidated_at TIMESTAMPTZ NULL,
                    revalidation_mode TEXT NOT NULL,
                    embedding VECTOR({self.embedding_provider.dimensions}) NULL,
                    embedding_provider TEXT NOT NULL DEFAULT '{self.embedding_provider.provider_name}',
                    embedding_dimensions INTEGER NOT NULL DEFAULT {self.embedding_provider.dimensions},
                    embedding_version TEXT NOT NULL DEFAULT '{self.embedding_provider.version}',
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    UNIQUE (repo, claim_key),
                    UNIQUE (repo, source_identity_key)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS claim_evidence (
                    evidence_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    run_id TEXT NULL,
                    evidence_kind TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    evidence_text TEXT NOT NULL DEFAULT '',
                    weight DOUBLE PRECISION NOT NULL DEFAULT 0,
                    observed_at TIMESTAMPTZ NOT NULL,
                    source_thread_id TEXT NULL,
                    source_path TEXT NULL,
                    source_entity_id TEXT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    PRIMARY KEY (repo, evidence_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS repo_core_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    compiled_at TIMESTAMPTZ NOT NULL,
                    source_watermark INTEGER NOT NULL DEFAULT 0,
                    blocks JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source_claim_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS dream_runs (
                    run_id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    run_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ NULL,
                    worker_id TEXT NULL,
                    cursor_before INTEGER NOT NULL DEFAULT 0,
                    cursor_after INTEGER NOT NULL DEFAULT 0,
                    signal_count INTEGER NOT NULL DEFAULT 0,
                    claim_candidate_count INTEGER NOT NULL DEFAULT 0,
                    merged_count INTEGER NOT NULL DEFAULT 0,
                    promoted_count INTEGER NOT NULL DEFAULT 0,
                    snapshot_id TEXT NULL,
                    summary JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS dreaming_leases (
                    repo TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_entity_revisions_repo_seq
                ON entity_revisions (repo, observed_seq DESC)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_repo_events_repo_seq
                ON repo_events (repo, observed_seq DESC)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_entities_repo_path
                ON entities (repo, path)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_memory_claims_repo_kind_status
                ON memory_claims (repo, claim_kind, status)
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_repo_core_snapshots_repo_compiled
                ON repo_core_snapshots (repo, compiled_at DESC)
                """,
            ]
            for statement in statements:
                await conn.execute(statement)
            await conn.execute(
                "ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS dreaming_cursor INTEGER NOT NULL DEFAULT 0"
            )
        finally:
            await conn.close()

    async def _ensure_repo_row(self, conn: asyncpg.Connection, repo: str) -> None:
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

    async def _bump_last_observed_seq(
        self, conn: asyncpg.Connection, repo: str, observed_seq: int
    ) -> None:
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
