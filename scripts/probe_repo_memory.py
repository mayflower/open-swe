"""End-to-end probe for repo memory + Dreaming against a real repository.

Walks a repo checkout, flushes every supported source file through the
production ``FlushCoordinator`` (Tree-sitter → entity revisions → embeddings →
pgvector), seeds a handful of diverse repo events, runs two Dreaming passes,
then prints the resulting claims, snapshot, vector-search output, and the
explain-mode report.

Usage (from the repo root, after ``make postgres-up`` and
``make repo-memory-migrate``)::

    uv run repo-memory-probe --path . --repo langchain-ai/open-swe

    # Or against a different checkout:
    uv run repo-memory-probe --path /path/to/other/repo --repo owner/name

Use ``--reset`` to truncate repo-memory tables before the run — handy when you
want a clean slate on re-runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import RepoEvent, RepoEventKind
from agent.repo_memory.dreaming import explain_dreaming_promotions, run_repo_memory_dreaming_pass
from agent.repo_memory.persistence.migrations import validate_repo_memory_schema
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import create_repo_memory_store
from agent.repo_memory.retrieval.search import search_store_similar_code_results
from agent.repo_memory.runtime import RepoMemoryRuntime, _RUNTIME_REGISTRY
from agent.repo_memory.sync import FlushCoordinator

logger = logging.getLogger("repo_memory_probe")

DEFAULT_POSTGRES_URL = "postgresql://open_swe:open_swe@localhost:5432/open_swe"
SUPPORTED_SUFFIXES = {".py", ".ts", ".tsx", ".go", ".rs"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
}

_SEED_EVENTS: list[dict] = [
    # Four near-identical watchout events from distinct operator threads.
    # Identical token sets trigger the Jaccard dedup (threshold 0.9) during the
    # Light phase, so all four collapse into ONE claim whose evidence list has
    # four distinct `source_thread_id`s — enough to clear the count + diversity
    # promotion gates in a single pass.
    {
        "kind": RepoEventKind.WATCHOUT,
        "summary": "Run uv run repo-memory-migrate before starting; skipping pgvector migrations breaks retrieval.",
        "thread_id": "oncall-notes-001",
    },
    {
        "kind": RepoEventKind.WATCHOUT,
        "summary": "Run uv run repo-memory-migrate before starting; skipping pgvector migrations breaks retrieval.",
        "thread_id": "oncall-notes-002",
    },
    {
        "kind": RepoEventKind.WATCHOUT,
        "summary": "Run uv run repo-memory-migrate before starting; skipping pgvector migrations breaks retrieval.",
        "thread_id": "oncall-notes-003",
    },
    {
        "kind": RepoEventKind.WATCHOUT,
        "summary": "Run uv run repo-memory-migrate before starting; skipping pgvector migrations breaks retrieval.",
        "thread_id": "oncall-notes-004",
    },
    # A second, distinct decision kept as-is so the report shows a non-promoted
    # claim and you can eyeball the gate failures too.
    {
        "kind": RepoEventKind.DECISION,
        "summary": "Entity parsing for supported languages runs through Tree-sitter, not regex.",
        "thread_id": "design-review-001",
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=Path.cwd(),
        help="Path to the repository to index. Defaults to the current directory.",
    )
    parser.add_argument(
        "--repo",
        default="probe/local",
        help="Repository identifier to scope memory by. Use owner/name format.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres connection string. Falls back to REPO_MEMORY_DATABASE_URL or the default local harness.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=None,
        choices=["openai", "hashed"],
        help=(
            "Embedding provider. Default: 'openai' if OPENAI_API_KEY is set, else 'hashed'. "
            "The hashed provider is deterministic and requires no API key."
        ),
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=None,
        help="Embedding vector width. Defaults: openai=1536, hashed=16.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=200,
        help="Upper bound on indexed files. Keeps runs snappy on large repos.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=32,
        help="Files per FlushCoordinator batch.",
    )
    parser.add_argument(
        "--query",
        default="shared normalization helpers and pgvector retrieval",
        help="Query sent through search_similar_code for the demo.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate repo-memory tables before running (clean-slate probe).",
    )
    parser.add_argument(
        "--skip-seed-events",
        action="store_true",
        help="Skip seeding dreaming events (only exercises understanding).",
    )
    parser.add_argument(
        "--skip-dreaming",
        action="store_true",
        help="Skip the Dreaming passes (only exercises understanding).",
    )
    return parser.parse_args()


def _iter_source_files(root: Path, *, max_files: int) -> Iterator[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            return
        if not path.is_file():
            continue
        if path.suffix not in SUPPORTED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path
        count += 1


def _reset_tables(database_url: str) -> None:
    async def _op() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(
                """
                TRUNCATE TABLE
                    claim_evidence,
                    dream_runs,
                    dreaming_leases,
                    entity_links,
                    entity_revisions,
                    entities,
                    file_revisions,
                    files,
                    memory_claims,
                    repo_events,
                    repo_core_blocks,
                    repo_core_snapshots,
                    sync_state,
                    repositories
                """
            )
        finally:
            await conn.close()

    asyncio.run(_op())


def _resolve_config(args: argparse.Namespace) -> tuple[str, RepoMemoryConfig]:
    database_url = (
        args.database_url
        or os.getenv("REPO_MEMORY_DATABASE_URL")
        or DEFAULT_POSTGRES_URL
    )
    provider = args.embedding_provider or (
        "openai" if os.getenv("OPENAI_API_KEY") else "hashed"
    )
    dimensions = args.embedding_dimensions or (1536 if provider == "openai" else 16)
    config = RepoMemoryConfig(
        backend="postgres",
        database_url=database_url,
        embedding_provider=provider,
        embedding_dimensions=dimensions,
    )
    return database_url, config


def _flush_source_tree(
    store: object,
    repo: str,
    root: Path,
    *,
    max_files: int,
    chunk_size: int,
) -> dict[str, int]:
    coordinator = FlushCoordinator(repo=repo, store=store)
    total_files = 0
    batch: dict[str, str] = {}
    observed_seq = store.get_sync_state(repo).get("last_observed_seq", 0) or 0
    for path in _iter_source_files(root, max_files=max_files):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning("skipping %s: %s", relative, exc)
            continue
        batch[relative] = content
        total_files += 1
        if len(batch) >= chunk_size:
            observed_seq += 1
            coordinator.flush(changed_files=batch, observed_seq=observed_seq, focus_paths=[])
            batch = {}
    if batch:
        observed_seq += 1
        coordinator.flush(changed_files=batch, observed_seq=observed_seq, focus_paths=[])
    return {"files": total_files, "last_observed_seq": observed_seq}


def _seed_events(store: object, repo: str, observed_seq: int) -> int:
    next_seq = observed_seq
    appended = 0
    for index, seed in enumerate(_SEED_EVENTS, start=1):
        next_seq += 1
        event = RepoEvent(
            repo=repo,
            event_id=f"probe:event:{index}",
            kind=seed["kind"],
            summary=seed["summary"],
            observed_seq=next_seq,
            metadata={"thread_id": seed["thread_id"]},
        )
        store.append_repo_event(event)
        appended += 1
    return appended


def _run_dreaming(runtime: RepoMemoryRuntime) -> list[dict]:
    base_now = datetime.now(UTC)
    runs: list[dict] = []
    for index in range(2):
        # Stagger the run time slightly so consolidation_score can climb.
        run = run_repo_memory_dreaming_pass(
            runtime,
            worker_id=f"probe:{index}",
            now=base_now + timedelta(minutes=index),
        )
        runs.append(
            {
                "run_id": run.run_id,
                "status": run.status,
                "cursor_before": run.cursor_before,
                "cursor_after": run.cursor_after,
                "promoted_count": run.promoted_count,
                "snapshot_id": run.snapshot_id,
                "summary": run.summary,
            }
        )
    return runs


def _summarize_claims(store: object, repo: str) -> list[dict]:
    summaries: list[dict] = []
    for claim in store.list_claims(repo):
        summaries.append(
            {
                "claim_key": claim.claim_key,
                "claim_kind": claim.claim_kind.value,
                "status": claim.status.value,
                "score": round(claim.score, 3),
                "scope": f"{claim.scope_kind.value}:{claim.scope_ref}",
                "text": claim.text,
                "evidence_count": len(store.list_claim_evidence(repo, claim.claim_key)),
                "failed_gates": claim.metadata.get("promotion_gate_failures", []),
            }
        )
    return summaries


def _summarize_snapshot(store: object, repo: str) -> dict | None:
    snapshot = store.get_latest_repo_core_snapshot(repo)
    if snapshot is None:
        return None
    return {
        "snapshot_id": snapshot.snapshot_id,
        "source_watermark": snapshot.source_watermark,
        "source_claim_keys": snapshot.source_claim_keys,
        "blocks": [
            {"label": block.label, "value": block.value[:280]}
            for block in snapshot.blocks
        ],
    }


def _summarize_search(
    store: object, repo: str, query: str, config: RepoMemoryConfig
) -> list[dict]:
    results = search_store_similar_code_results(
        store, repo, query, config=config, limit=5
    )
    return [
        {
            "qualified_name": result.entity.qualified_name,
            "path": result.entity.path,
            "language": result.entity.language,
            "score": round(result.score, 3),
            "explanation": result.explanation,
        }
        for result in results
    ]


def _summarize_explain(runtime: RepoMemoryRuntime) -> list[dict]:
    explanations = explain_dreaming_promotions(runtime)
    payload: list[dict] = []
    for explanation in explanations:
        data = asdict(explanation)
        data["score"] = round(data["score"], 3)
        payload.append(data)
    return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    args = _parse_args()
    root = args.path.expanduser().resolve()
    if not root.is_dir():
        logger.error("--path %s is not a directory", root)
        return 2

    database_url, config = _resolve_config(args)
    os.environ.setdefault("REPO_MEMORY_ALLOW_IN_MEMORY", "false")

    try:
        validate_repo_memory_schema(
            database_url, vector_dimensions=config.embedding_dimensions
        )
    except Exception as exc:
        logger.error(
            "repo-memory schema is not ready at %s: %s. "
            "Run `make postgres-up && make repo-memory-migrate`.",
            database_url,
            exc,
        )
        return 2

    if args.reset:
        logger.info("resetting repo-memory tables at %s", database_url)
        _reset_tables(database_url)

    # Fresh runtime registry so the probe does not inherit state from prior runs.
    _RUNTIME_REGISTRY.clear()
    store = create_repo_memory_store(config)
    assert isinstance(store, PostgresRepoMemoryStore)
    runtime = RepoMemoryRuntime(repo=args.repo, store=store, config=config)

    logger.info("flushing source tree under %s (max_files=%d)", root, args.max_files)
    flush_summary = _flush_source_tree(
        store,
        args.repo,
        root,
        max_files=args.max_files,
        chunk_size=args.chunk_size,
    )

    if not args.skip_seed_events:
        seeded = _seed_events(store, args.repo, flush_summary["last_observed_seq"])
        logger.info("seeded %d repo events", seeded)

    dreaming_runs: list[dict] = []
    if not args.skip_dreaming and not args.skip_seed_events:
        logger.info("running Dreaming passes")
        dreaming_runs = _run_dreaming(runtime)

    report = {
        "repo": args.repo,
        "database_url": database_url,
        "embedding": {
            "provider": config.embedding_provider,
            "dimensions": config.embedding_dimensions,
        },
        "flush": flush_summary,
        "dreaming_runs": dreaming_runs,
        "claims": _summarize_claims(store, args.repo),
        "snapshot": _summarize_snapshot(store, args.repo),
        "similar_code": _summarize_search(store, args.repo, args.query, config),
        "explain": _summarize_explain(runtime) if not args.skip_seed_events else [],
    }
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
