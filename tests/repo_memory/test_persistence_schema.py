from agent.repo_memory.persistence.models import build_metadata


def test_metadata_contains_expected_tables() -> None:
    metadata = build_metadata()
    assert set(metadata.tables) == {
        "claim_evidence",
        "dream_runs",
        "dreaming_leases",
        "repositories",
        "files",
        "file_revisions",
        "entities",
        "entity_revisions",
        "entity_links",
        "memory_claims",
        "repo_events",
        "repo_core_blocks",
        "repo_core_snapshots",
        "sync_state",
    }


def test_entity_revisions_table_exposes_embedding_column() -> None:
    metadata = build_metadata()
    assert metadata.tables["entity_revisions"].columns["embedding"].type_name == "vector"


def test_sync_state_exposes_dreaming_cursor() -> None:
    metadata = build_metadata()
    assert metadata.tables["sync_state"].columns["dreaming_cursor"].type_name == "int"


def test_memory_claims_expose_embedding_column() -> None:
    metadata = build_metadata()
    assert metadata.tables["memory_claims"].columns["embedding"].type_name == "vector"
