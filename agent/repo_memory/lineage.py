from __future__ import annotations

from dataclasses import dataclass

from .matching import MatchDecision


@dataclass(slots=True)
class LineageRecord:
    entity_id: str
    predecessor_id: str
    reason: str
    confidence: float


def lineage_from_match(match: MatchDecision) -> LineageRecord:
    return LineageRecord(
        entity_id=match.new_entity_id,
        predecessor_id=match.old_entity_id,
        reason=match.reason,
        confidence=match.confidence,
    )

