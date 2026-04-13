from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import before_model
from langgraph.runtime import Runtime

from ..compiler import compile_core_memory_blocks, render_repo_memory_message
from ..config import RepoMemoryConfig
from ..state import RepoMemoryState

logger = logging.getLogger(__name__)


def build_injection_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    runtime = state.get("repo_memory_runtime", {})
    store = runtime.get("store")
    repo = runtime.get("repo")
    config = runtime.get("config") or RepoMemoryConfig()
    if not store or not repo:
        return None
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
        "repo_memory_injection repo=%s block_count=%d message_words=%d",
        repo,
        len(blocks),
        len(message.split()),
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
    return build_injection_payload(state)
