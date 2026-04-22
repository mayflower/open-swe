from __future__ import annotations

import pytest

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import (
    InMemoryRepoMemoryStore,
    create_repo_memory_store,
)


def test_missing_database_url_and_in_memory_opt_in_refuses_to_start_production_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPO_MEMORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPO_MEMORY_BACKEND", raising=False)
    monkeypatch.delenv("REPO_MEMORY_ALLOW_IN_MEMORY", raising=False)

    config = RepoMemoryConfig()

    assert config.resolved_backend() == "unconfigured"
    with pytest.raises(ValueError):
        create_repo_memory_store(config)


def test_explicit_allow_in_memory_opts_into_the_in_memory_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPO_MEMORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("REPO_MEMORY_BACKEND", raising=False)
    monkeypatch.setenv("REPO_MEMORY_ALLOW_IN_MEMORY", "true")

    config = RepoMemoryConfig()

    assert config.resolved_backend() == "memory"
    assert isinstance(create_repo_memory_store(config), InMemoryRepoMemoryStore)


def test_postgres_backend_requires_database_url() -> None:
    config = RepoMemoryConfig(backend="postgres", database_url=None)

    with pytest.raises(ValueError):
        create_repo_memory_store(config)


def test_postgres_backend_returns_postgres_store() -> None:
    config = RepoMemoryConfig(
        backend="postgres",
        database_url="postgresql://user:pass@localhost:5432/db",
        embedding_provider="hashed",
        embedding_dimensions=16,
    )

    store = create_repo_memory_store(config)

    assert isinstance(store, PostgresRepoMemoryStore)
