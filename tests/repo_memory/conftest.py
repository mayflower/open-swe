from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import asyncpg
import pytest

os.environ.setdefault("REPO_MEMORY_ALLOW_IN_MEMORY", "true")

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.persistence.migrations import apply_repo_memory_migrations
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import create_repo_memory_store
from agent.repo_memory.runtime import _RUNTIME_REGISTRY

DEFAULT_POSTGRES_URL = "postgresql://open_swe:open_swe@localhost:5432/open_swe"
REPO_ROOT = Path(__file__).resolve().parents[2]
_POSTGRES_HARNESS_STATUS: dict[str, tuple[bool, str | None]] = {}


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


def _ensure_postgres_service(database_url: str) -> None:
    cached = _POSTGRES_HARNESS_STATUS.get(database_url)
    if cached is not None:
        ok, error = cached
        if ok:
            return
        raise RuntimeError(error or f"Postgres harness previously failed for {database_url}")
    if _can_connect(database_url):
        _POSTGRES_HARNESS_STATUS[database_url] = (True, None)
        return
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.postgres.yml", "up", "-d", "postgres"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    deadline = time.time() + 45
    while time.time() < deadline:
        if _can_connect(database_url):
            _POSTGRES_HARNESS_STATUS[database_url] = (True, None)
            return
        time.sleep(1)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    error = (
        "Postgres repo-memory tests require a reachable pgvector database and could not start "
        f"the local compose harness at {database_url}. "
        f"docker compose exit code: {result.returncode}. "
        f"stdout: {stdout or '<empty>'}. stderr: {stderr or '<empty>'}."
    )
    _POSTGRES_HARNESS_STATUS[database_url] = (False, error)
    raise RuntimeError(error)


@pytest.fixture(autouse=True)
def clear_runtime_registry() -> None:
    _RUNTIME_REGISTRY.clear()


@pytest.fixture
def postgres_url() -> str:
    return os.getenv("REPO_MEMORY_DATABASE_URL", DEFAULT_POSTGRES_URL)


@pytest.fixture
def postgres_store(postgres_url: str) -> PostgresRepoMemoryStore:
    _ensure_postgres_service(postgres_url)
    apply_repo_memory_migrations(postgres_url, vector_dimensions=16)
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
