from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import create_repo_memory_store
from agent.repo_memory.runtime import _RUNTIME_REGISTRY

DEFAULT_POSTGRES_URL = "postgresql://open_swe:open_swe@localhost:5432/open_swe"


def _can_connect(database_url: str) -> bool:
    async def _probe() -> bool:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute("SELECT 1")
            return True
        finally:
            await conn.close()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


@pytest.fixture(autouse=True)
def clear_runtime_registry() -> None:
    _RUNTIME_REGISTRY.clear()


@pytest.fixture
def postgres_url() -> str:
    return os.getenv("REPO_MEMORY_DATABASE_URL", DEFAULT_POSTGRES_URL)


@pytest.fixture
def postgres_store(postgres_url: str) -> PostgresRepoMemoryStore:
    if not _can_connect(postgres_url):
        pytest.skip(f"Postgres repo-memory tests require a running database at {postgres_url}")
    config = RepoMemoryConfig(
        backend="postgres",
        database_url=postgres_url,
        embedding_provider="hashed",
        embedding_dimensions=16,
    )
    store = create_repo_memory_store(config)
    assert isinstance(store, PostgresRepoMemoryStore)
    store.get_sync_state("fixture")

    async def _reset() -> None:
        conn = await asyncpg.connect(postgres_url)
        try:
            await conn.execute(
                """
                TRUNCATE TABLE
                    claim_evidence,
                    dream_runs,
                    dreaming_leases,
                    entity_links,
                    entity_revisions,
                    entities,
                    file_revisions,
                    files,
                    memory_claims,
                    repo_events,
                    repo_core_blocks,
                    repo_core_snapshots,
                    sync_state,
                    repositories
                """
            )
        finally:
            await conn.close()

    asyncio.run(_reset())
    return store
