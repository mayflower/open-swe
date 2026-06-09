from agent.repo_memory.domain import EntityKind, EntityRevision
from agent.repo_memory.matching import match_entities


def _revision(entity_id: str, path: str, name: str, observed_seq: int) -> EntityRevision:
    return EntityRevision(
        entity_id=entity_id,
        repo="repo",
        path=path,
        language="python",
        kind=EntityKind.FUNCTION,
        name=name,
        qualified_name=name,
        observed_seq=observed_seq,
        signature=f"def {name}(value)",
        retrieval_text=name,
    )


def test_high_confidence_match_preserves_identity() -> None:
    decision = match_entities(
        _revision("old", "a.py", "helper", 1),
        _revision("new", "a.py", "helper", 2),
    )
    assert decision.preserve_identity is True
    assert decision.confidence >= 0.7


def test_low_confidence_match_creates_predecessor_link() -> None:
    decision = match_entities(
        _revision("old", "a.py", "helper", 1),
        _revision("new", "b.py", "transform", 2),
    )
    assert decision.preserve_identity is False
    assert decision.confidence < 0.7
