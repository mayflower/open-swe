from __future__ import annotations

from ..config import RepoMemoryConfig


def score_candidate(
    query_tokens: set[str],
    retrieval_text: str,
    *,
    same_language: bool,
    same_kind: bool,
    freshness: int,
    config: RepoMemoryConfig,
) -> float:
    text_tokens = {token.lower() for token in retrieval_text.split()}
    score = float(len(query_tokens & text_tokens))
    if same_language:
        score += config.same_language_bonus
    if same_kind:
        score += config.same_kind_bonus
    score += min(freshness / 100.0, config.freshness_bonus)
    return score

