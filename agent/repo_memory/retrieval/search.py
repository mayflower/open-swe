from __future__ import annotations

from dataclasses import dataclass

from ..config import RepoMemoryConfig
from ..domain import EntityRevision
from ..embeddings import build_embedding_provider
from .ranking import score_candidate


@dataclass(slots=True)
class SimilarCodeResult:
    entity: EntityRevision
    score: float
    explanation: str


def search_similar_code_results(
    candidates: list[EntityRevision],
    query: str,
    *,
    config: RepoMemoryConfig,
    current_path: str | None = None,
    current_entity_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> list[SimilarCodeResult]:
    query_tokens = {token.lower() for token in query.split() if token}
    ranked: list[SimilarCodeResult] = []
    for candidate in candidates:
        if current_path and candidate.path == current_path:
            continue
        if current_entity_id and candidate.entity_id == current_entity_id:
            continue
        same_language = language is not None and candidate.language == language
        same_kind = kind is not None and candidate.kind.value == kind
        score = score_candidate(
            query_tokens,
            candidate.retrieval_text,
            same_language=same_language,
            same_kind=same_kind,
            freshness=candidate.observed_seq,
            config=config,
        )
        if score <= 0:
            continue
        reasons: list[str] = ["lexical_only"]
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
    max_results = limit or config.max_similarity_results
    return ranked[:max_results]


def search_store_similar_code_results(
    store: object,
    repo: str,
    query: str,
    *,
    config: RepoMemoryConfig,
    current_path: str | None = None,
    current_entity_id: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> list[SimilarCodeResult]:
    max_results = limit or config.max_similarity_results
    if not hasattr(store, "search_vector_entities"):
        return search_similar_code_results(
            list(store.iter_entities(repo)),
            query,
            config=config,
            current_path=current_path,
            current_entity_id=current_entity_id,
            language=language,
            kind=kind,
            limit=limit,
        )

    provider = getattr(store, "embedding_provider", None) or build_embedding_provider(config)
    hits = store.search_vector_entities(
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
    return ranked[:max_results]
