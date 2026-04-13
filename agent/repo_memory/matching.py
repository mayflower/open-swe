from __future__ import annotations

from dataclasses import dataclass

from .domain import EntityRevision


@dataclass(slots=True)
class MatchDecision:
    old_entity_id: str
    new_entity_id: str
    confidence: float
    preserve_identity: bool
    reason: str


def match_entities(old: EntityRevision, new: EntityRevision) -> MatchDecision:
    confidence = 0.0
    reasons: list[str] = []
    if old.kind == new.kind:
        confidence += 0.4
        reasons.append("same kind")
    if old.name == new.name:
        confidence += 0.3
        reasons.append("same name")
    if old.path == new.path:
        confidence += 0.2
        reasons.append("same path")
    if old.signature == new.signature:
        confidence += 0.2
        reasons.append("same signature")
    preserve_identity = confidence >= 0.7
    return MatchDecision(
        old_entity_id=old.entity_id,
        new_entity_id=new.entity_id,
        confidence=confidence,
        preserve_identity=preserve_identity,
        reason=", ".join(reasons),
    )

