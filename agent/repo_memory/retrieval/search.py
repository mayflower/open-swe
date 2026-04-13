from __future__ import annotations

from dataclasses import dataclass

from ..config import RepoMemoryConfig
from ..domain import EntityRevision
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
        reasons: list[str] = []
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
                explanation=", ".join(reasons) or "text overlap",
            )
        )
    ranked.sort(
        key=lambda item: (-item.score, -item.entity.observed_seq, item.entity.qualified_name)
    )
    max_results = limit or config.max_similarity_results
    return ranked[:max_results]
