from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .config import RepoMemoryConfig
from .dreaming import (
    PromotionExplanation,
    explain_dreaming_promotions,
    run_repo_memory_dreaming_pass,
    supports_dreaming,
)
from .embeddings import build_embedding_provider
from .persistence.migrations import validate_repo_memory_schema
from .persistence.repositories import create_repo_memory_store
from .runtime import RepoMemoryRuntime

if TYPE_CHECKING:
    from .domain import DreamRun

logger = logging.getLogger(__name__)


def discover_dreaming_repos(store: object) -> list[str]:
    if not supports_dreaming(store) or not hasattr(store, "list_repositories"):
        return []
    repos = store.list_repositories()
    return sorted({repo for repo in repos if isinstance(repo, str) and repo})


def explain_repo_memory_dreaming(
    store: object,
    repo: str,
    *,
    config: RepoMemoryConfig,
    now: datetime | None = None,
) -> list[PromotionExplanation]:
    runtime = RepoMemoryRuntime(repo=repo, store=store, config=config)
    return explain_dreaming_promotions(runtime, now=now)


def reembed_repo_memory_repo(
    store: object,
    repo: str,
    *,
    config: RepoMemoryConfig,
) -> dict[str, int]:
    provider = build_embedding_provider(config)
    entity_count = 0
    claim_count = 0

    if hasattr(store, "iter_entities") and hasattr(store, "upsert_entity_revision"):
        for revision in store.iter_entities(repo):
            store.upsert_entity_revision(revision)
            entity_count += 1

    if hasattr(store, "list_claims") and hasattr(store, "upsert_claim"):
        claims = list(store.list_claims(repo))
        embeddings = provider.embed_many([claim.text for claim in claims])
        for claim, embedding in zip(claims, embeddings, strict=False):
            updated = replace(
                claim,
                embedding=embedding,
                embedding_provider=provider.provider_name,
                embedding_dimensions=provider.dimensions,
                embedding_version=provider.version,
            )
            store.upsert_claim(updated)
            claim_count += 1

    return {"entities": entity_count, "claims": claim_count}


def reembed_repo_memory_all_repos(
    store: object,
    *,
    config: RepoMemoryConfig,
) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    for repo in discover_dreaming_repos(store):
        results[repo] = reembed_repo_memory_repo(store, repo, config=config)
    return results


def run_repo_memory_dreaming_cycle(
    store: object,
    *,
    config: RepoMemoryConfig,
    now: datetime | None = None,
    worker_prefix: str = "dreaming-daemon",
) -> list[DreamRun]:
    runs: list[DreamRun] = []
    cycle_now = now or datetime.now(UTC)
    for repo in discover_dreaming_repos(store):
        runtime = RepoMemoryRuntime(repo=repo, store=store, config=config)
        try:
            runs.append(
                run_repo_memory_dreaming_pass(
                    runtime,
                    worker_id=f"{worker_prefix}:{repo}",
                    now=cycle_now,
                )
            )
        except Exception:
            logger.exception("repo_memory_dreaming_cycle_failed repo=%s", repo)
    return runs


def run_repo_memory_dreaming_daemon(
    *,
    config: RepoMemoryConfig,
    iterations: int | None = None,
    sleep_seconds: int | None = None,
    sleep_fn: Callable[[float], object] = time.sleep,
) -> list[list[DreamRun]]:
    store = create_repo_memory_store(config)
    cycles: list[list[DreamRun]] = []
    remaining = iterations
    pause = (
        sleep_seconds
        if sleep_seconds is not None
        else config.dreaming_daemon_poll_interval_seconds
    )
    while remaining is None or remaining > 0:
        cycles.append(run_repo_memory_dreaming_cycle(store, config=config))
        if remaining is not None:
            remaining -= 1
            if remaining <= 0:
                break
        sleep_fn(pause)
    return cycles


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the repo-memory Dreaming daemon.")
    parser.add_argument("--backend", default=None, help="Repo-memory backend to use.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL for the durable repo-memory store.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Polling interval in seconds between daemon cycles.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single daemon cycle and exit.",
    )
    parser.add_argument(
        "--reembed-all",
        action="store_true",
        help="Recompute all stored claim/entity embeddings for every discovered repo and exit.",
    )
    parser.add_argument(
        "--reembed-repo",
        default=None,
        help="Recompute stored claim/entity embeddings for one repo and exit.",
    )
    parser.add_argument(
        "--explain",
        default=None,
        help=(
            "Dry-run the Deep-phase scoring for one repo. Prints each claim's "
            "score, gate outcomes, and would-promote verdict without mutating "
            "claims or writing a snapshot."
        ),
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    config = RepoMemoryConfig()
    if args.backend:
        config.backend = args.backend
    if args.database_url:
        config.database_url = args.database_url
    if args.poll_interval is not None:
        config.dreaming_daemon_poll_interval_seconds = args.poll_interval
    if config.resolved_backend() != "postgres":
        logger.error("The standalone Dreaming daemon requires the Postgres repo-memory backend.")
        return 2
    if not config.database_url:
        logger.error("The standalone Dreaming daemon requires REPO_MEMORY_DATABASE_URL.")
        return 2
    validate_repo_memory_schema(
        config.database_url,
        vector_dimensions=config.embedding_dimensions,
    )
    store = create_repo_memory_store(config)
    if args.reembed_repo:
        summary = reembed_repo_memory_repo(store, args.reembed_repo, config=config)
        logger.info(
            "repo_memory_reembed_complete repo=%s entities=%d claims=%d",
            args.reembed_repo,
            summary["entities"],
            summary["claims"],
        )
        return 0
    if args.explain:
        explanations = explain_repo_memory_dreaming(store, args.explain, config=config)
        for explanation in explanations:
            logger.info(
                "repo_memory_dreaming_explain repo=%s claim=%s kind=%s status=%s "
                "score=%.3f would_promote=%s failed_gates=%s",
                args.explain,
                explanation.claim_key,
                explanation.claim_kind,
                explanation.status,
                explanation.score,
                explanation.would_promote,
                ",".join(explanation.failed_gates) or "-",
            )
        return 0
    if args.reembed_all:
        summaries = reembed_repo_memory_all_repos(store, config=config)
        for repo, summary in summaries.items():
            logger.info(
                "repo_memory_reembed_complete repo=%s entities=%d claims=%d",
                repo,
                summary["entities"],
                summary["claims"],
            )
        return 0
    run_repo_memory_dreaming_daemon(
        config=config,
        iterations=1 if args.once else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
