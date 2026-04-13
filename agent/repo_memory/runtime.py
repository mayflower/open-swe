from __future__ import annotations

from dataclasses import dataclass, field

from .config import RepoMemoryConfig
from .persistence.repositories import InMemoryRepoMemoryStore


@dataclass(slots=True)
class RepoMemoryRuntime:
    repo: str
    store: InMemoryRepoMemoryStore = field(default_factory=InMemoryRepoMemoryStore)
    config: RepoMemoryConfig = field(default_factory=RepoMemoryConfig)


DEFAULT_RUNTIME = RepoMemoryRuntime(repo="unknown")

