from __future__ import annotations

from dataclasses import dataclass

from .domain import RepoEvent, RepoEventKind, make_repo_event_id


@dataclass(slots=True)
class RepoEventSearchResult:
    event: RepoEvent
    score: float
    explanation: str


def remember_decision_event(
    repo: str,
    observed_seq: int,
    summary: str,
    path: str | None = None,
    entity_id: str | None = None,
    evidence_refs: list[str] | None = None,
) -> RepoEvent:
    return RepoEvent(
        repo=repo,
        event_id=make_repo_event_id(repo, observed_seq, RepoEventKind.DECISION),
        kind=RepoEventKind.DECISION,
        summary=summary.strip(),
        observed_seq=observed_seq,
        path=path,
        entity_id=entity_id,
        evidence_refs=list(evidence_refs or []),
    )


def search_repo_events(
    events: list[RepoEvent],
    query: str,
    path: str | None = None,
    entity_id: str | None = None,
    limit: int = 5,
) -> list[RepoEventSearchResult]:
    query_tokens = {token.lower() for token in query.split() if token}
    ranked: list[RepoEventSearchResult] = []
    for event in events:
        summary_tokens = {token.lower() for token in event.summary.split()}
        score = float(len(query_tokens & summary_tokens))
        reasons: list[str] = []
        if path and event.path == path:
            score += 2.0
            reasons.append("same path")
        if entity_id and event.entity_id == entity_id:
            score += 2.0
            reasons.append("same entity")
        if event.kind == RepoEventKind.DECISION:
            score += 0.5
            reasons.append("decision event")
        if score <= 0:
            continue
        ranked.append(
            RepoEventSearchResult(
                event=event,
                score=score,
                explanation=", ".join(reasons) or "text overlap",
            )
        )
    ranked.sort(key=lambda item: (-item.score, -item.event.observed_seq))
    return ranked[:limit]

