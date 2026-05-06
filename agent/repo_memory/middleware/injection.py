from __future__ import annotations

import logging
from typing import Any

try:
    from langchain.agents.middleware import before_model
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs

    def before_model(*args, **kwargs):  # type: ignore[override]
        def decorator(fn):
            return fn

        return decorator


try:
    from langgraph.config import get_config
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs

    def get_config() -> dict[str, Any]:
        raise RuntimeError("langgraph is not available")


try:
    from langgraph.runtime import Runtime
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs

    class Runtime:
        pass


from ..compiler import compile_core_memory_blocks, render_repo_memory_message
from ..config import RepoMemoryConfig
from ..dreaming import (
    abuild_snapshot_injection_blocks,
    build_snapshot_injection_blocks,
    supports_dreaming,
)
from ..persistence.notifier import (
    ensure_listener_started,
    freshness_token,
    is_listener_ready,
    is_stale,
)
from ..runtime import resolve_runtime_from_context, runtime_attr
from ..state import RepoMemoryState
from ..sync import aflush_runtime_state, flush_runtime_state

logger = logging.getLogger(__name__)


def _maybe_start_listener(store: object) -> str | None:
    """Start the listener for the store's database_url, if any.

    Returns the database_url so callers can check listener readiness after.
    Errors during startup degrade gracefully — the cache will be bypassed
    when the listener isn't ready (see ``_can_use_cache``).
    """
    database_url = getattr(store, "database_url", None)
    if not isinstance(database_url, str) or not database_url:
        return None
    try:
        ensure_listener_started(database_url)
    except Exception:
        logger.exception(
            "repo_memory_listener_bootstrap_failed url=%s — caching disabled until listener recovers",
            database_url,
        )
    return database_url


def _can_use_cache(database_url: str | None) -> bool:
    """``True`` only if we have a working invalidation channel.

    Without a live listener the version counters can't be trusted — every
    cached payload could be silently stale. Force a fresh fetch in that
    case.
    """
    if database_url is None:
        return True  # In-memory store: no notifications, but no parallel writers either.
    return is_listener_ready(database_url)


async def _store_call(store: object, sync_name: str, *args, **kwargs):
    """Prefer ``a<sync_name>`` if the store exposes an async sibling.

    The async path submits work to the shared asyncpg loop and ``await``s it
    without blocking the agent's event loop. The sync fallback is correct
    for in-memory stores where the work is pure Python and returns
    immediately.
    """
    async_method = getattr(store, f"a{sync_name}", None)
    if async_method is not None:
        return await async_method(*args, **kwargs)
    return getattr(store, sync_name)(*args, **kwargs)


def build_injection_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    """Sync wrapper kept for back-compat / tests.

    Production callers should prefer :func:`abuild_injection_payload` so the
    agent's event loop never blocks on store / pool I/O.
    """
    runtime = resolve_runtime_from_context(state)
    store = runtime_attr(runtime, "store")
    repo = runtime_attr(runtime, "repo")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not store or not repo:
        return None
    state["repo_memory_runtime"] = runtime
    database_url = _maybe_start_listener(store)
    flushed = flush_runtime_state(state, runtime)

    cache_slot = _resolve_cache_slot(state, repo)
    cached = cache_slot.get(repo) if cache_slot is not None else None
    # Capture the freshness token *before* the fetch so a peer write that
    # commits during the fetch invalidates the next call instead of being
    # masked by the post-fetch token.
    versions_at_start = dict(freshness_token(repo))
    if (
        cached is not None
        and not flushed
        and _can_use_cache(database_url)
        and not is_stale(repo, cached.get("versions"))
    ):
        return cached["payload"]

    blocks = None
    if supports_dreaming(store):
        blocks = build_snapshot_injection_blocks(
            store,
            repo,
            config=config,
            focus_paths=state.get("focus_paths", []),
            focus_entities=state.get("focus_entities", []),
        )
    if blocks is None:
        events = store.list_repo_events(repo)
        blocks = compile_core_memory_blocks(
            repo,
            events,
            config.core_block_token_budgets,
            focus_paths=state.get("focus_paths", []),
            focus_entities=state.get("focus_entities", []),
        )
        for block in blocks:
            store.set_core_block(repo, block)
    return _finalize_injection(
        state, repo, blocks, flushed, cache_slot, versions_at_start, database_url
    )


async def abuild_injection_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    """Async injection path used by ``inject_repo_memory_before_model``.

    Hot-path methods are awaited via store ``a*`` siblings so the agent's
    event loop yields while pool work runs on the shared asyncpg loop. Falls
    back to sync calls for stores that don't yet expose the async surface.
    """
    runtime = resolve_runtime_from_context(state)
    store = runtime_attr(runtime, "store")
    repo = runtime_attr(runtime, "repo")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not store or not repo:
        return None
    state["repo_memory_runtime"] = runtime
    database_url = _maybe_start_listener(store)
    flushed = await aflush_runtime_state(state, runtime)

    cache_slot = _resolve_cache_slot(state, repo)
    cached = cache_slot.get(repo) if cache_slot is not None else None
    versions_at_start = dict(freshness_token(repo))
    if (
        cached is not None
        and not flushed
        and _can_use_cache(database_url)
        and not is_stale(repo, cached.get("versions"))
    ):
        return cached["payload"]

    blocks = None
    if supports_dreaming(store):
        blocks = await abuild_snapshot_injection_blocks(
            store,
            repo,
            config=config,
            focus_paths=state.get("focus_paths", []),
            focus_entities=state.get("focus_entities", []),
        )
    if blocks is None:
        events = await _store_call(store, "list_repo_events", repo)
        blocks = compile_core_memory_blocks(
            repo,
            events,
            config.core_block_token_budgets,
            focus_paths=state.get("focus_paths", []),
            focus_entities=state.get("focus_entities", []),
        )
        for block in blocks:
            await _store_call(store, "set_core_block", repo, block)
    return _finalize_injection(
        state, repo, blocks, flushed, cache_slot, versions_at_start, database_url
    )


def _resolve_cache_slot(state: dict[str, Any], repo: str) -> dict[str, Any] | None:
    """Return a usable cache dict for ``state`` or ``None`` if the slot was
    clobbered to a non-dict shape (which we log so a state-shape regression
    doesn't take weeks to discover).
    """
    slot = state.setdefault("_repo_memory_injection_cache", {})
    if isinstance(slot, dict):
        return slot
    logger.warning(
        "repo_memory_injection_cache_slot_invalid type=%s repo=%s — resetting",
        type(slot).__name__,
        repo,
    )
    fresh: dict[str, Any] = {}
    state["_repo_memory_injection_cache"] = fresh
    return fresh


def _finalize_injection(
    state: dict[str, Any],
    repo: str,
    blocks: list,
    flushed: list[str],
    cache_slot: dict[str, Any] | None,
    versions_at_start: dict[str, int],
    database_url: str | None,
) -> dict[str, Any]:
    message = render_repo_memory_message(blocks)
    logger.info(
        "repo_memory_injection repo=%s block_count=%d message_words=%d flushed=%d",
        repo,
        len(blocks),
        len(message.split()),
        len(flushed),
    )
    payload = {
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": message}],
            }
        ]
    }
    # Only cache when we have a working invalidation channel — without it
    # the version vector is meaningless and the cache becomes a stale-data
    # generator.
    if cache_slot is not None and _can_use_cache(database_url):
        cache_slot[repo] = {"payload": payload, "versions": versions_at_start}
    return payload


@before_model(state_schema=RepoMemoryState)
async def inject_repo_memory_before_model(
    state: RepoMemoryState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    try:
        config = get_config()
    except RuntimeError as exc:
        # The stub raises ``RuntimeError("langgraph is not available")`` in
        # stripped test envs. Anything else is a misconfiguration we should
        # surface, not silently swallow.
        if "langgraph is not available" not in str(exc):
            logger.warning("repo_memory_injection_get_config_failed err=%s", exc)
        config = {}
    except Exception:
        logger.exception("repo_memory_injection_get_config_unexpected")
        config = {}
    metadata = config.get("metadata", {})
    if (
        resolve_runtime_from_context(state) is None
        and metadata.get("repo_memory_runtime") is not None
    ):
        state["repo_memory_runtime"] = metadata["repo_memory_runtime"]
    return await abuild_injection_payload(state)
