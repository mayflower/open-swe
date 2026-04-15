from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..config import RepoMemoryConfig
from ..domain import CodeEntity, EntityRevision, FileRevision, RepoCoreBlock, RepoEvent, RepoFile
from ..embeddings import build_embedding_provider
from .models import MemoryMetadata, build_metadata
from .postgres import PostgresRepoMemoryStore


@dataclass(slots=True)
class InMemoryRepoMemoryStore:
    metadata: MemoryMetadata = field(default_factory=build_metadata)
    _files: dict[tuple[str, str], RepoFile] = field(init=False, default_factory=dict)
    _entities: dict[str, CodeEntity] = field(init=False, default_factory=dict)
    _events: dict[str, list[RepoEvent]] = field(
        init=False,
        default_factory=lambda: defaultdict(list),
    )
    _core_blocks: dict[str, dict[str, RepoCoreBlock]] = field(
        init=False,
        default_factory=lambda: defaultdict(dict),
    )
    _sync_state: dict[str, dict[str, int]] = field(
        init=False,
        default_factory=lambda: defaultdict(
            lambda: {"last_observed_seq": 0, "last_compiled_seq": 0}
        ),
    )
    _lineage: list[dict[str, Any]] = field(init=False, default_factory=list)

    def upsert_file_revision(self, revision: FileRevision) -> None:
        key = (revision.repo, revision.path)
        current = self._files.get(key)
        if current is None:
            self._files[key] = RepoFile(
                repo=revision.repo,
                path=revision.path,
                language=revision.language,
                current_revision=revision,
            )
        else:
            current.observe(revision)
        self._sync_state[revision.repo]["last_observed_seq"] = max(
            self._sync_state[revision.repo]["last_observed_seq"],
            revision.observed_seq,
        )

    def get_file(self, repo: str, path: str) -> RepoFile | None:
        return self._files.get((repo, path))

    def upsert_entity_revision(self, revision: EntityRevision) -> None:
        current = self._entities.get(revision.entity_id)
        if current is None:
            self._entities[revision.entity_id] = CodeEntity(
                entity_id=revision.entity_id,
                repo=revision.repo,
                path=revision.path,
                language=revision.language,
                kind=revision.kind,
                current_revision=revision,
            )
        else:
            current.observe(revision)
        self._sync_state[revision.repo]["last_observed_seq"] = max(
            self._sync_state[revision.repo]["last_observed_seq"],
            revision.observed_seq,
        )

    def get_entity(self, entity_id: str) -> CodeEntity | None:
        return self._entities.get(entity_id)

    def iter_entities(self, repo: str) -> list[EntityRevision]:
        revisions: list[EntityRevision] = []
        for entity in self._entities.values():
            if entity.repo == repo:
                revisions.append(entity.current_revision)
        return revisions

    def append_repo_event(self, event: RepoEvent) -> None:
        self._events[event.repo].append(event)
        self._sync_state[event.repo]["last_observed_seq"] = max(
            self._sync_state[event.repo]["last_observed_seq"],
            event.observed_seq,
        )

    def list_repo_events(self, repo: str) -> list[RepoEvent]:
        return list(self._events.get(repo, []))

    def set_core_block(self, repo: str, block: RepoCoreBlock) -> None:
        self._core_blocks[repo][block.label] = block

    def list_core_blocks(self, repo: str) -> list[RepoCoreBlock]:
        return list(self._core_blocks.get(repo, {}).values())

    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:
        self._sync_state[repo]["last_compiled_seq"] = observed_seq

    def get_sync_state(self, repo: str) -> dict[str, int]:
        return dict(self._sync_state[repo])

    def record_lineage(
        self, entity_id: str, predecessor_id: str, reason: str, confidence: float
    ) -> None:
        self._lineage.append(
            {
                "entity_id": entity_id,
                "predecessor_id": predecessor_id,
                "reason": reason,
                "confidence": confidence,
            }
        )
        entity = self._entities.get(entity_id)
        if entity and predecessor_id not in entity.predecessor_ids:
            entity.predecessor_ids.append(predecessor_id)

    def list_lineage(self) -> list[dict[str, Any]]:
        return list(self._lineage)


def create_repo_memory_store(config: RepoMemoryConfig) -> InMemoryRepoMemoryStore | PostgresRepoMemoryStore:
    if config.resolved_backend() == "postgres":
        if not config.database_url:
            raise ValueError("REPO_MEMORY_DATABASE_URL is required for postgres repo memory")
        return PostgresRepoMemoryStore(
            database_url=config.database_url,
            embedding_provider=build_embedding_provider(config),
        )
    return InMemoryRepoMemoryStore()
