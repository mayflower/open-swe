"""Process-wide asyncpg pool + dedicated event loop.

Why this exists
---------------
``asyncpg.Pool`` is bound to one event loop. The original repo-memory store
opened a connection per call (``asyncpg.connect``/``close``) and ran each call
through ``asyncio.run`` — when invoked from inside a running loop it even
spawned a thread + new loop *per call*. Under massively parallel agents that
pattern saturates Postgres ``max_connections`` and serializes Python tasks
behind thread spawn / TLS handshake cost.

This module owns:

1. A single background event loop pinned to a daemon thread (``_LoopThread``)
   so all asyncpg work shares one loop and one set of connections.
2. A pool registry keyed by ``database_url`` so every ``PostgresRepoMemoryStore``
   instance reuses the same pool.
3. ``run_async`` / ``arun`` helpers: callers use ``run_async`` when they live
   on a different loop (sync code or another asyncio loop) and ``arun`` when
   they want a non-blocking ``await`` on the shared loop's future.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

import asyncpg

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_POOL_MIN_SIZE = _env_int("REPO_MEMORY_POOL_MIN_SIZE", 1)
_POOL_MAX_SIZE = _env_int("REPO_MEMORY_POOL_MAX_SIZE", 20)
_POOL_COMMAND_TIMEOUT = _env_int("REPO_MEMORY_POOL_COMMAND_TIMEOUT_SECONDS", 60)


class _LoopThread:
    """Long-lived background event loop shared by every asyncpg call."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and self._loop.is_running():
            return self._loop
        with self._start_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._thread = threading.Thread(
                target=_run,
                name="repo-memory-asyncpg-loop",
                daemon=True,
            )
            self._thread.start()
            ready.wait()
            assert self._loop is not None
            return self._loop

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        loop = self.loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    async def arun(self, coro: Coroutine[Any, Any, T]) -> T:
        loop = self.loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return await asyncio.wrap_future(future)


_LOOP_THREAD = _LoopThread()
_POOLS: dict[str, asyncpg.Pool] = {}
_POOLS_LOCK = threading.Lock()


async def _create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=_POOL_MIN_SIZE,
        max_size=_POOL_MAX_SIZE,
        command_timeout=_POOL_COMMAND_TIMEOUT,
    )
    logger.info(
        "repo_memory_pool_created url=%s min_size=%d max_size=%d command_timeout=%ds",
        database_url,
        _POOL_MIN_SIZE,
        _POOL_MAX_SIZE,
        _POOL_COMMAND_TIMEOUT,
    )
    return pool


def pool_stats() -> dict[str, dict[str, int]]:
    """Per-database snapshot of pool size and free slots — useful for metrics."""
    snapshot: dict[str, dict[str, int]] = {}
    with _POOLS_LOCK:
        pools = dict(_POOLS)
    for url, pool in pools.items():
        try:
            snapshot[url] = {
                "size": pool.get_size(),
                "max_size": pool.get_max_size(),
                "min_size": pool.get_min_size(),
                "idle": pool.get_idle_size(),
            }
        except Exception:
            continue
    return snapshot


def get_pool(database_url: str) -> asyncpg.Pool:
    """Return (creating once) the shared asyncpg pool for ``database_url``.

    Pool creation happens on the dedicated loop thread so the pool's bound loop
    is the one every subsequent call uses. Two callers that race the cache
    miss only create one pool — the loser's pool is closed.

    Refuses to create a pool on cache miss when called from inside the
    dedicated loop itself: that would submit a coroutine to the loop we're
    running on and deadlock. Callers that may run on-loop must pre-warm the
    pool from outside the loop first (see ``PostgresRepoMemoryStore._ensure_schema``).
    """
    with _POOLS_LOCK:
        existing = _POOLS.get(database_url)
        if existing is not None:
            return existing
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is _LOOP_THREAD._loop:
        raise RuntimeError(
            "repo_memory pool not warmed for "
            f"{database_url!r} — call get_pool from outside the asyncpg loop "
            "before issuing on-loop work (PostgresRepoMemoryStore._ensure_schema "
            "handles this for the standard call paths)"
        )
    pool = _LOOP_THREAD.run(_create_pool(database_url))
    with _POOLS_LOCK:
        cached = _POOLS.get(database_url)
        if cached is not None:
            _LOOP_THREAD.run(pool.close())
            return cached
        _POOLS[database_url] = pool
        return pool


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` on the shared loop and block until it returns.

    Use from sync callers or from a different event loop. Calling this from
    code that is already running on the shared loop will deadlock — async
    code on the shared loop should ``await`` directly.
    """
    return _LOOP_THREAD.run(coro)


async def arun(coro: Coroutine[Any, Any, T]) -> T:
    """Schedule ``coro`` on the shared loop and ``await`` it from any loop."""
    return await _LOOP_THREAD.arun(coro)


def close_all_pools() -> None:
    """Close every cached pool. Call during process shutdown / test teardown."""
    with _POOLS_LOCK:
        pools = list(_POOLS.items())
        _POOLS.clear()
    ok = 0
    failed = 0
    for url, pool in pools:
        try:
            _LOOP_THREAD.run(pool.close())
            ok += 1
        except Exception:
            failed += 1
            logger.exception("repo_memory_pool_close_failed url=%s", url)
    logger.info(
        "repo_memory_pool_closed_all ok=%d failed=%d total=%d",
        ok,
        failed,
        len(pools),
    )


def reset_pool_for_tests(database_url: str) -> None:
    """Test-only: drop the cached pool so the next caller rebuilds it.

    Close failures are logged at debug rather than swallowed silently —
    persistent close failures usually mean a test left a transaction open.
    """
    with _POOLS_LOCK:
        pool = _POOLS.pop(database_url, None)
    if pool is not None:
        try:
            _LOOP_THREAD.run(pool.close())
        except Exception:
            logger.debug(
                "repo_memory_pool_reset_close_failed url=%s",
                database_url,
                exc_info=True,
            )
