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
from ..dreaming import build_snapshot_injection_blocks, supports_dreaming
from ..runtime import resolve_runtime_from_context, runtime_attr
from ..state import RepoMemoryState
from ..sync import flush_runtime_state

logger = logging.getLogger(__name__)


def build_injection_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    runtime = resolve_runtime_from_context(state)
    store = runtime_attr(runtime, "store")
    repo = runtime_attr(runtime, "repo")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not store or not repo:
        return None
    state["repo_memory_runtime"] = runtime
    flushed = flush_runtime_state(state, runtime)
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
    message = render_repo_memory_message(blocks)
    logger.info(
        "repo_memory_injection repo=%s block_count=%d message_words=%d flushed=%d",
        repo,
        len(blocks),
        len(message.split()),
        len(flushed),
    )
    return {
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": message}],
            }
        ]
    }


@before_model(state_schema=RepoMemoryState)
async def inject_repo_memory_before_model(
    state: RepoMemoryState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    try:
        config = get_config()
    except Exception:
        config = {}
    metadata = config.get("metadata", {})
    if resolve_runtime_from_context(state) is None and metadata.get("repo_memory_runtime") is not None:
        state["repo_memory_runtime"] = metadata["repo_memory_runtime"]
    return build_injection_payload(state)
