from __future__ import annotations

from typing import Any

from ..repo_memory.config import RepoMemoryConfig
from ..repo_memory.embeddings import build_embedding_provider
from ..repo_memory.retrieval.ranking import score_candidate
from ..repo_memory.retrieval.search import (
    SimilarCodeResult,
    search_store_similar_code_results,
)
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
    return _to_payload(results)


async def asearch_similar_code(
    query: str,
    current_path: str | None = None,
    current_entity_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Async sibling — uses ``asearch_vector_entities`` so the agent loop
    yields while pgvector cosine search runs on the asyncpg pool loop.
    """
    runtime = resolve_runtime_from_context()
    repo = runtime_attr(runtime, "repo", "unknown")
    store = runtime_attr(runtime, "store")
    if store is None:
        return {"results": []}
    config: RepoMemoryConfig = (
        runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    )
    max_results = limit or config.max_similarity_results

    if hasattr(store, "asearch_vector_entities"):
        provider = getattr(store, "embedding_provider", None) or build_embedding_provider(config)
        hits = await store.asearch_vector_entities(
            repo,
            provider.embed(query),
            current_path=current_path,
            current_entity_id=current_entity_id,
            limit=max(max_results * 4, max_results),
        )
        query_tokens = {token.lower() for token in query.split() if token}
        ranked: list[SimilarCodeResult] = []
        for hit in hits:
            candidate = hit.entity
            same_language = language is not None and candidate.language == language
            same_kind = kind is not None and candidate.kind.value == kind
            lexical_score = score_candidate(
                query_tokens,
                candidate.retrieval_text,
                same_language=same_language,
                same_kind=same_kind,
                freshness=candidate.observed_seq,
                config=config,
            )
            score = lexical_score + (max(hit.similarity, 0.0) * 10.0)
            reasons: list[str] = [f"vector={hit.similarity:.3f}"]
            if same_language:
                reasons.append("same language")
            if same_kind:
                reasons.append("same kind")
            if candidate.observed_seq:
                reasons.append(f"freshness={candidate.observed_seq}")
            ranked.append(
                SimilarCodeResult(
                    entity=candidate,
                    score=score,
                    explanation=", ".join(reasons),
                )
            )
        ranked.sort(
            key=lambda item: (-item.score, -item.entity.observed_seq, item.entity.qualified_name)
        )
        return _to_payload(ranked[:max_results])

    return search_similar_code(
        query,
        current_path=current_path,
        current_entity_id=current_entity_id,
        language=language,
        kind=kind,
        limit=limit,
    )


def _to_payload(results: list[SimilarCodeResult]) -> dict[str, Any]:
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
