from __future__ import annotations

from typing import Any

from ..repo_memory.config import RepoMemoryConfig
from ..repo_memory.domain import ClaimKind, ClaimScopeKind
from ..repo_memory.embeddings import build_embedding_provider
from ..repo_memory.events import search_repo_events
from ..repo_memory.runtime import resolve_runtime_from_context, runtime_attr


def search_repo_memory(
    query: str,
    claim_kind: str | None = None,
    scope_kind: str | None = None,
    scope_ref: str | None = None,
    include_events: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Search persisted repo memory (claims + events) for the current repo.

    When the runtime is backed by pgvector this uses true vector similarity over
    claim embeddings. Repo events are searched with lexical ranking since they
    are not embedded today; results are marked accordingly so callers can tell.
    """
    runtime = resolve_runtime_from_context()
    repo = runtime_attr(runtime, "repo", "unknown")
    store = runtime_attr(runtime, "store")
    if store is None:
        return {"repo": repo, "claims": [], "events": [], "retrieval": "unavailable"}

    config: RepoMemoryConfig = (
        runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    )
    max_results = limit or config.max_event_search_results

    claim_kind_enum = ClaimKind(claim_kind) if claim_kind else None
    scope_kind_enum = ClaimScopeKind(scope_kind) if scope_kind else None

    claim_hits: list[dict[str, Any]] = []
    retrieval = "no_vector_support"
    if hasattr(store, "find_related_claims") and hasattr(store, "embedding_provider"):
        provider = store.embedding_provider
        query_embedding = provider.embed(query)
        related = store.find_related_claims(
            repo,
            query_embedding,
            claim_kind=claim_kind_enum,
            scope_kind=scope_kind_enum,
            scope_ref=scope_ref,
            limit=max_results,
        )
        claim_hits = [_claim_to_payload(claim, similarity) for claim, similarity in related]
        retrieval = "pgvector" if type(store).__name__ == "PostgresRepoMemoryStore" else "vector"
    elif hasattr(store, "find_related_claims"):
        provider = build_embedding_provider(config)
        query_embedding = provider.embed(query)
        related = store.find_related_claims(
            repo,
            query_embedding,
            claim_kind=claim_kind_enum,
            scope_kind=scope_kind_enum,
            scope_ref=scope_ref,
            limit=max_results,
        )
        claim_hits = [_claim_to_payload(claim, similarity) for claim, similarity in related]
        retrieval = "in_memory_vector"

    event_hits: list[dict[str, Any]] = []
    if include_events and hasattr(store, "list_repo_events"):
        events = store.list_repo_events(repo)
        for hit in search_repo_events(events, query, limit=max_results):
            event_hits.append(
                {
                    "event_id": hit.event.event_id,
                    "kind": hit.event.kind.value,
                    "summary": hit.event.summary,
                    "score": hit.score,
                    "explanation": f"lexical_only; {hit.explanation}",
                    "path": hit.event.path,
                    "entity_id": hit.event.entity_id,
                    "observed_seq": hit.event.observed_seq,
                }
            )

    return {
        "repo": repo,
        "claims": claim_hits,
        "events": event_hits,
        "retrieval": retrieval,
    }


def _claim_to_payload(claim: Any, similarity: float) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "claim_key": claim.claim_key,
        "claim_kind": claim.claim_kind.value,
        "scope_kind": claim.scope_kind.value,
        "scope_ref": claim.scope_ref,
        "status": claim.status.value,
        "score": claim.score,
        "text": claim.text,
        "similarity": similarity,
        "explanation": f"vector={similarity:.3f}",
    }
