from __future__ import annotations

from typing import Any

from ..repo_memory.config import RepoMemoryConfig
from ..repo_memory.retrieval.search import search_store_similar_code_results
from ..repo_memory.runtime import resolve_runtime_from_context, runtime_attr


def search_similar_code(
    query: str,
    current_path: str | None = None,
    current_entity_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Search repo memory for reusable code."""
    runtime = resolve_runtime_from_context()
    repo = runtime_attr(runtime, "repo", "unknown")
    store = runtime_attr(runtime, "store")
    if store is None:
        return {"results": []}
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    results = search_store_similar_code_results(
        store,
        repo,
        query,
        config=config,
        current_path=current_path,
        current_entity_id=current_entity_id,
        language=language,
        kind=kind,
        limit=limit,
    )
    return {
        "results": [
            {
                "entity_id": result.entity.entity_id,
                "qualified_name": result.entity.qualified_name,
                "path": result.entity.path,
                "score": result.score,
                "explanation": result.explanation,
                "freshness": result.entity.observed_seq,
            }
            for result in results
        ]
    }
