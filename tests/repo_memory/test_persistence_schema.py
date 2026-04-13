from agent.repo_memory.persistence.models import build_metadata


def test_metadata_contains_expected_tables() -> None:
    metadata = build_metadata()
    assert set(metadata.tables) == {
        "repositories",
        "files",
        "file_revisions",
        "entities",
        "entity_revisions",
        "entity_links",
        "repo_events",
        "repo_core_blocks",
        "sync_state",
    }


def test_entity_revisions_table_exposes_embedding_column() -> None:
    metadata = build_metadata()
    assert metadata.tables["entity_revisions"].columns["embedding"].type_name == "vector"
