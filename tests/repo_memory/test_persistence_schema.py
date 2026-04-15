import asyncio

import asyncpg

from agent.repo_memory.persistence.migrations import (
    latest_repo_memory_schema_version,
    validate_repo_memory_schema,
)
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


def test_postgres_schema_validation_uses_latest_migration(
    postgres_store,
    postgres_url: str,
) -> None:
    assert postgres_store is not None
    version = validate_repo_memory_schema(postgres_url, vector_dimensions=16)

    async def _fetch_applied_versions() -> list[str]:
        conn = await asyncpg.connect(postgres_url)
        try:
            rows = await conn.fetch(
                """
                SELECT version
                FROM repo_memory_schema_migrations
                ORDER BY version ASC
                """
            )
        finally:
            await conn.close()
        return [row["version"] for row in rows]

    assert version == latest_repo_memory_schema_version()
    assert asyncio.run(_fetch_applied_versions()) == [version]
