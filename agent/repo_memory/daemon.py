from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .config import RepoMemoryConfig
from .dreaming import run_repo_memory_dreaming_pass, supports_dreaming
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
    run_repo_memory_dreaming_daemon(
        config=config,
        iterations=1 if args.once else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
