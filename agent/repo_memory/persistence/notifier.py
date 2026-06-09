"""Process-wide LISTEN/NOTIFY listener for cross-worker invalidation.

Why this exists
---------------
With many agents writing to the same repo concurrently, a worker that has
already loaded the latest snapshot has no signal that a peer just promoted a
new claim. Polling on every model call works (the middleware does it) but
adds DB round-trips even when nothing changed. Postgres ``LISTEN/NOTIFY``
gives us the wakeup for free: migration 0002 installs triggers that publish
``pg_notify(channel, repo)`` after ``INSERT`` on ``repo_events`` and after
``INSERT`` or ``UPDATE`` on ``memory_claims`` / ``repo_core_snapshots``.

This module owns one long-lived asyncpg connection per ``database_url`` that
listens on those three channels and bumps an in-process version counter
keyed on ``(repo, channel)``. Consumers compare the version against what
they last saw — if equal, the cached state is still fresh.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from collections.abc import Mapping

import asyncpg

from .pool import run_async

logger = logging.getLogger(__name__)


CHANNELS: tuple[str, ...] = (
    "repo_memory_event",
    "repo_memory_claim",
    "repo_memory_snapshot",
)


_VERSIONS: dict[tuple[str, str], int] = defaultdict(int)
_VERSIONS_LOCK = threading.Lock()
_LISTENERS_STARTED: set[str] = set()
_LISTENER_CONNECTIONS: dict[str, asyncpg.Connection] = {}
_LISTENER_TASKS: dict[str, asyncio.Task] = {}
_LISTENERS_LOCK = threading.Lock()

_RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0
_HEARTBEAT_INTERVAL_SECONDS = 5.0
_HEARTBEAT_TIMEOUT_SECONDS = 10.0
_CONSECUTIVE_FAILURES_FOR_ALERT = 5
# A connection that died within this window after first connect counts as
# "flapping" — we delay the next reconnect attempt instead of resetting backoff.
_MIN_HEALTHY_CONNECTION_SECONDS = 30.0


_LISTENERS_READY: set[str] = set()


def _bump(repo: str, channel: str) -> None:
    if not repo:
        return
    with _VERSIONS_LOCK:
        _VERSIONS[(repo, channel)] += 1


def _make_callback(channel: str):
    def _cb(_connection, _pid, _ch, payload) -> None:
        _bump(payload or "", channel)

    return _cb


def get_versions(repo: str) -> dict[str, int]:
    """Return ``{channel: version}`` for ``repo``.

    The version is a monotonically increasing counter — equality means
    nothing has been published on that channel since the last call.
    """
    with _VERSIONS_LOCK:
        snapshot: dict[str, int] = {}
        for channel in CHANNELS:
            snapshot[channel] = _VERSIONS.get((repo, channel), 0)
        return snapshot


def freshness_token(repo: str) -> tuple[tuple[str, int], ...]:
    """Compact, comparable token of the current version vector for ``repo``."""
    versions = get_versions(repo)
    return tuple(sorted(versions.items()))


def is_stale(repo: str, last_seen: Mapping[str, int] | None) -> bool:
    """``True`` if any channel has bumped past ``last_seen``."""
    if not last_seen:
        return True
    versions = get_versions(repo)
    for channel in CHANNELS:
        if versions.get(channel, 0) > last_seen.get(channel, 0):
            return True
    return False


def is_listener_ready(database_url: str) -> bool:
    """``True`` if the supervisor for ``database_url`` is currently subscribed.

    Callers that depend on LISTEN/NOTIFY for cache freshness should check this
    before reusing a cached payload — when the listener is not ready the
    version counters can't be trusted and the cache must be bypassed.
    """
    with _LISTENERS_LOCK:
        return database_url in _LISTENERS_READY


async def _connect_and_listen(database_url: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn=database_url)
    for channel in CHANNELS:
        await conn.add_listener(channel, _make_callback(channel))
    return conn


async def _supervise(database_url: str, ready: asyncio.Event) -> None:
    """Keep one listener connection alive across reconnects.

    The connection is held passively (no app ops, only ``add_listener``
    callbacks). To detect silent failures — server-side ``pg_terminate_backend``,
    NAT idle-timeout, half-closed sockets where ``is_closed()`` stays False —
    we issue a ``SELECT 1`` heartbeat on a fixed interval; any error or
    timeout breaks the inner loop and triggers reconnect with exponential
    backoff. ``ready`` is set on the first successful connect so callers can
    block until the listener is actually subscribed.
    """
    import time as _time

    backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
    consecutive_failures = 0
    while True:
        try:
            conn = await _connect_and_listen(database_url)
        except Exception:
            consecutive_failures += 1
            logger.exception(
                "repo_memory_listener_connect_failed url=%s backoff=%.1fs failures=%d",
                database_url,
                backoff,
                consecutive_failures,
            )
            if consecutive_failures == _CONSECUTIVE_FAILURES_FOR_ALERT:
                logger.error(
                    "repo_memory_listener_unrecoverable url=%s consecutive_failures=%d "
                    "— consumers will keep serving cached state until the listener "
                    "reconnects or the cache is invalidated by a fresh write",
                    database_url,
                    consecutive_failures,
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
            continue
        with _LISTENERS_LOCK:
            _LISTENER_CONNECTIONS[database_url] = conn
            _LISTENERS_READY.add(database_url)
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        consecutive_failures = 0
        ready.set()
        connected_at = _time.monotonic()
        logger.info("repo_memory_listener_connected url=%s", database_url)
        try:
            while not conn.is_closed():
                try:
                    await asyncio.wait_for(
                        conn.execute("SELECT 1"),
                        timeout=_HEARTBEAT_TIMEOUT_SECONDS,
                    )
                except (TimeoutError, asyncpg.PostgresError, OSError) as exc:
                    logger.warning(
                        "repo_memory_listener_heartbeat_failed url=%s err=%s",
                        database_url,
                        exc,
                    )
                    break
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        finally:
            try:
                await conn.close()
            except asyncpg.InterfaceError:
                pass  # Already closed — expected when server side terminated.
            except Exception:
                logger.debug(
                    "repo_memory_listener_close_failed url=%s",
                    database_url,
                    exc_info=True,
                )
            with _LISTENERS_LOCK:
                _LISTENER_CONNECTIONS.pop(database_url, None)
                _LISTENERS_READY.discard(database_url)
        # If the connection died in under _MIN_HEALTHY_CONNECTION_SECONDS we
        # have a flapping postgres; sleep one backoff interval before
        # reconnecting so we don't burn CPU spinning on a degraded server.
        if _time.monotonic() - connected_at < _MIN_HEALTHY_CONNECTION_SECONDS:
            logger.warning(
                "repo_memory_listener_short_lived_connection url=%s lifetime=%.1fs",
                database_url,
                _time.monotonic() - connected_at,
            )
            await asyncio.sleep(_RECONNECT_BACKOFF_INITIAL_SECONDS)
        else:
            logger.warning(
                "repo_memory_listener_disconnected url=%s — reconnecting",
                database_url,
            )


def ensure_listener_started(database_url: str) -> None:
    """Start the background listener for ``database_url`` exactly once.

    Blocks until the first ``LISTEN`` completes so callers can write events
    immediately after this returns and observe the resulting notification.
    On connection loss the supervisor reconnects with exponential backoff.
    """
    with _LISTENERS_LOCK:
        if database_url in _LISTENERS_STARTED:
            return
        _LISTENERS_STARTED.add(database_url)

    ready: asyncio.Event | None = None

    async def _spawn() -> asyncio.Event:
        ev = asyncio.Event()
        task = asyncio.create_task(
            _supervise(database_url, ev),
            name=f"repo-memory-listener:{database_url}",
        )
        with _LISTENERS_LOCK:
            _LISTENER_TASKS[database_url] = task
        return ev

    try:
        ready = run_async(_spawn())
    except Exception:
        logger.exception("repo_memory_listener_start_failed url=%s", database_url)
        with _LISTENERS_LOCK:
            _LISTENERS_STARTED.discard(database_url)
        return

    try:
        run_async(asyncio.wait_for(ready.wait(), timeout=10.0))
    except Exception:
        logger.warning(
            "repo_memory_listener_first_connect_timeout url=%s — supervisor will keep retrying",
            database_url,
        )


async def stop_all_listeners() -> None:
    """Cancel every supervisor task and close every connection.

    Runs on the shared asyncpg loop so ``task.cancel()`` is thread-safe.
    """
    with _LISTENERS_LOCK:
        connections = list(_LISTENER_CONNECTIONS.items())
        tasks = list(_LISTENER_TASKS.values())
        _LISTENER_CONNECTIONS.clear()
        _LISTENER_TASKS.clear()
        _LISTENERS_STARTED.clear()
        _LISTENERS_READY.clear()
    for task in tasks:
        task.cancel()
    for url, conn in connections:
        try:
            await conn.close()
        except asyncpg.InterfaceError:
            pass  # Connection already closed — expected during teardown.
        except Exception:
            logger.debug("repo_memory_listener_close_failed url=%s", url, exc_info=True)


def shutdown() -> None:
    """Synchronous shutdown — safe to call from FastAPI shutdown hooks."""
    try:
        run_async(stop_all_listeners())
    except RuntimeError as exc:
        # Loop already torn down by an earlier shutdown step. Nothing to do.
        logger.debug("repo_memory_listener_shutdown_after_loop_close err=%s", exc)
    except Exception:
        logger.exception("repo_memory_listener_shutdown_failed")


def reset_for_tests() -> None:
    """Test-only teardown. Cancels supervisor tasks via ``call_soon_threadsafe``
    so cancellation runs on the loop the tasks belong to.
    """
    with _VERSIONS_LOCK:
        _VERSIONS.clear()
    with _LISTENERS_LOCK:
        connections = list(_LISTENER_CONNECTIONS.values())
        tasks = list(_LISTENER_TASKS.values())
        _LISTENER_CONNECTIONS.clear()
        _LISTENER_TASKS.clear()
        _LISTENERS_STARTED.clear()
        _LISTENERS_READY.clear()
    for task in tasks:
        loop = task.get_loop()
        loop.call_soon_threadsafe(task.cancel)
    for conn in connections:
        try:
            run_async(conn.close())
        except asyncpg.InterfaceError:
            pass
        except Exception:
            logger.debug("repo_memory_listener_reset_close_failed", exc_info=True)


__all__ = [
    "CHANNELS",
    "ensure_listener_started",
    "freshness_token",
    "get_versions",
    "is_listener_ready",
    "is_stale",
    "reset_for_tests",
    "shutdown",
    "stop_all_listeners",
]
