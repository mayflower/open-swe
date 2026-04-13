from agent.repo_memory.lineage import lineage_from_match
from agent.repo_memory.matching import MatchDecision


def test_lineage_record_captures_predecessor_information() -> None:
    record = lineage_from_match(
        MatchDecision(
            old_entity_id="old",
            new_entity_id="new",
            confidence=0.45,
            preserve_identity=False,
            reason="same kind",
        )
    )

    assert record.entity_id == "new"
    assert record.predecessor_id == "old"
    assert record.confidence == 0.45
