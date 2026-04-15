from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

import asyncpg

from ..domain import (
    CodeEntity,
    EntityKind,
    EntityRevision,
    FileRevision,
    RepoCoreBlock,
    RepoEvent,
    RepoEventKind,
    RepoFile,
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


@dataclass(slots=True)
class PostgresRepoMemoryStore:
    database_url: str
    embedding_provider: EmbeddingProvider
    metadata: MemoryMetadata = field(default_factory=build_metadata)
    _schema_ready: bool = field(init=False, default=False)
    _schema_lock: threading.Lock = field(init=False, default_factory=threading.Lock)

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

    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        async def _op() -> None:
            conn = await asyncpg.connect(self.database_url)
            try:
                async with conn.transaction():
                    await self._ensure_repo_row(conn, repo)
                    await conn.execute(
                        """
                        INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq)
                        VALUES ($1, 0, $2)
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
                    SELECT last_observed_seq, last_compiled_seq
                    FROM sync_state
                    WHERE repo = $1
                    """,
                    repo,
                )
            finally:
                await conn.close()
            if row is None:
                return {"last_observed_seq": 0, "last_compiled_seq": 0}
            return {
                "last_observed_seq": row["last_observed_seq"],
                "last_compiled_seq": row["last_compiled_seq"],
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
                    last_compiled_seq INTEGER NOT NULL DEFAULT 0
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
            ]
            for statement in statements:
                await conn.execute(statement)
        finally:
            await conn.close()

    async def _ensure_repo_row(self, conn: asyncpg.Connection, repo: str) -> None:
        await conn.execute(
            "INSERT INTO repositories (repo) VALUES ($1) ON CONFLICT (repo) DO NOTHING",
            repo,
        )
        await conn.execute(
            """
            INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq)
            VALUES ($1, 0, 0)
            ON CONFLICT (repo) DO NOTHING
            """,
            repo,
        )

    async def _bump_last_observed_seq(
        self, conn: asyncpg.Connection, repo: str, observed_seq: int
    ) -> None:
        await conn.execute(
            """
            INSERT INTO sync_state (repo, last_observed_seq, last_compiled_seq)
            VALUES ($1, $2, 0)
            ON CONFLICT (repo) DO UPDATE
            SET last_observed_seq = GREATEST(
                sync_state.last_observed_seq,
                EXCLUDED.last_observed_seq
            )
            """,
            repo,
            observed_seq,
        )
