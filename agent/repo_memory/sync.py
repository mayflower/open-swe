from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Any

from .config import RepoMemoryConfig
from .delta import parse_name_status_diff
from .domain import FileRevision
from .embeddings import EmbeddingProvider
from .matching import match_entities
from .parsing.common import ParsedEntity
from .parsing.go_parser import parse_go_entities
from .parsing.python_parser import parse_python_revisions
from .parsing.retrieval_text import build_retrieval_text
from .parsing.rust_parser import parse_rust_entities
from .parsing.typescript_parser import parse_typescript_entities
from .runtime import runtime_attr

logger = logging.getLogger(__name__)

_GIT_CHANGED_PATHS_COMMAND = (
    "git diff --name-status --relative; "
    "git diff --name-status --cached --relative; "
    "git ls-files --others --exclude-standard | sed 's#^#A\t#'"
)


@dataclass(slots=True)
class FlushCoordinator:
    repo: str
    store: object
    embedding_provider: EmbeddingProvider | None = None

    def detect_changed_paths(
        self, diff_output: str, focus_paths: list[str] | None = None
    ) -> list[str]:
        focus_paths = focus_paths or []
        changed = parse_name_status_diff(diff_output)
        prioritized = [path for path in focus_paths if path in changed]
        tail = [path for path in changed if path not in prioritized]
        return prioritized + tail

    def _entities_for_path(self, path: str) -> list:
        if hasattr(self.store, "iter_entities_for_path"):
            return list(self.store.iter_entities_for_path(self.repo, path))
        return [entity for entity in self.store.iter_entities(self.repo) if entity.path == path]

    def flush(
        self,
        *,
        changed_files: dict[str, str],
        observed_seq: int,
        focus_paths: list[str] | None = None,
    ) -> list[str]:
        ordered_paths, staged = self._stage_revisions(
            changed_files=changed_files,
            observed_seq=observed_seq,
            focus_paths=focus_paths,
        )
        all_revisions = [rev for _path, _content, revs in staged for rev in revs]
        if hasattr(self.store, "upsert_entity_revisions") and all_revisions:
            self.store.upsert_entity_revisions(all_revisions)
        else:
            for revision in all_revisions:
                self.store.upsert_entity_revision(revision)
        self.store.set_last_compiled_seq(self.repo, observed_seq)
        logger.info(
            "repo_memory_flush repo=%s dirty_count=%d observed_seq=%d",
            self.repo,
            len(ordered_paths),
            observed_seq,
        )
        return ordered_paths

    async def aflush(
        self,
        *,
        changed_files: dict[str, str],
        observed_seq: int,
        focus_paths: list[str] | None = None,
    ) -> list[str]:
        """Async sibling — DB writes go through ``a*`` store methods so the
        caller's event loop yields. Sandbox / parsing / embedding work stays
        sync (it doesn't touch asyncpg).
        """
        ordered_paths, staged = await self._astage_revisions(
            changed_files=changed_files,
            observed_seq=observed_seq,
            focus_paths=focus_paths,
        )
        all_revisions = [rev for _path, _content, revs in staged for rev in revs]
        if hasattr(self.store, "aupsert_entity_revisions") and all_revisions:
            await self.store.aupsert_entity_revisions(all_revisions)
        elif hasattr(self.store, "upsert_entity_revisions") and all_revisions:
            self.store.upsert_entity_revisions(all_revisions)
        else:
            for revision in all_revisions:
                self.store.upsert_entity_revision(revision)
        if hasattr(self.store, "aset_last_compiled_seq"):
            await self.store.aset_last_compiled_seq(self.repo, observed_seq)
        else:
            self.store.set_last_compiled_seq(self.repo, observed_seq)
        logger.info(
            "repo_memory_flush repo=%s dirty_count=%d observed_seq=%d",
            self.repo,
            len(ordered_paths),
            observed_seq,
        )
        return ordered_paths

    def _stage_revisions(
        self,
        *,
        changed_files: dict[str, str],
        observed_seq: int,
        focus_paths: list[str] | None,
    ) -> tuple[list[str], list[tuple[str, str, list]]]:
        focus_paths = focus_paths or []
        ordered_paths = [path for path in focus_paths if path in changed_files] + [
            path for path in changed_files if path not in focus_paths
        ]
        # Stage entity revisions per path so we can run a single batched
        # embedding call before any DB writes — avoids one OpenAI round-trip
        # per entity in the hot flush path.
        staged: list[tuple[str, str, list]] = []
        for path in ordered_paths:
            content = changed_files[path]
            self.store.upsert_file_revision(
                FileRevision(
                    repo=self.repo,
                    path=path,
                    language=_language_from_path(path),
                    observed_seq=observed_seq,
                    content=content,
                )
            )
            old_entities = {entity.entity_id: entity for entity in self._entities_for_path(path)}
            revisions = _parse_path(self.repo, path, content, observed_seq)
            for revision in revisions:
                best_match = self._best_match(old_entities.values(), revision)
                if best_match and best_match.preserve_identity:
                    revision.entity_id = best_match.old_entity_id
                elif best_match:
                    self.store.record_lineage(
                        revision.entity_id,
                        best_match.old_entity_id,
                        best_match.reason,
                        best_match.confidence,
                    )
            staged.append((path, content, revisions))
        return ordered_paths, staged

    async def _astage_revisions(
        self,
        *,
        changed_files: dict[str, str],
        observed_seq: int,
        focus_paths: list[str] | None,
    ) -> tuple[list[str], list[tuple[str, str, list]]]:
        focus_paths = focus_paths or []
        ordered_paths = [path for path in focus_paths if path in changed_files] + [
            path for path in changed_files if path not in focus_paths
        ]
        staged: list[tuple[str, str, list]] = []
        for path in ordered_paths:
            content = changed_files[path]
            file_revision = FileRevision(
                repo=self.repo,
                path=path,
                language=_language_from_path(path),
                observed_seq=observed_seq,
                content=content,
            )
            if hasattr(self.store, "aupsert_file_revision"):
                await self.store.aupsert_file_revision(file_revision)
            else:
                self.store.upsert_file_revision(file_revision)
            if hasattr(self.store, "aiter_entities_for_path"):
                old_iter = await self.store.aiter_entities_for_path(self.repo, path)
            else:
                old_iter = self._entities_for_path(path)
            old_entities = {entity.entity_id: entity for entity in old_iter}
            revisions = _parse_path(self.repo, path, content, observed_seq)
            for revision in revisions:
                best_match = self._best_match(old_entities.values(), revision)
                if best_match and best_match.preserve_identity:
                    revision.entity_id = best_match.old_entity_id
                elif best_match:
                    if hasattr(self.store, "arecord_lineage"):
                        await self.store.arecord_lineage(
                            revision.entity_id,
                            best_match.old_entity_id,
                            best_match.reason,
                            best_match.confidence,
                        )
                    else:
                        self.store.record_lineage(
                            revision.entity_id,
                            best_match.old_entity_id,
                            best_match.reason,
                            best_match.confidence,
                        )
            staged.append((path, content, revisions))
        return ordered_paths, staged

    @staticmethod
    def _best_match(previous_entities, revision):
        best_match = None
        for previous in previous_entities:
            decision = match_entities(previous, revision)
            if best_match is None or decision.confidence > best_match.confidence:
                best_match = decision
        return best_match


def _prepare_flush(
    state: dict[str, Any], runtime: object
) -> tuple[str, Any, list[str], dict[str, str], list[str]] | None:
    """Compute (repo, store, focus_paths, changed_files, dirty_paths) for flush.

    Returns ``None`` when no work is required (no runtime context, no dirty
    paths, no readable files).  Mutates ``state['dirty_paths']`` /
    ``state['dirty_unknown']`` when paths were enumerated but unreadable —
    that part is async-safe to do up front because it only touches state and
    the sandbox backend, not the store.
    """
    repo = runtime_attr(runtime, "repo")
    store = runtime_attr(runtime, "store")
    backend = runtime_attr(runtime, "sandbox_backend")
    work_dir = runtime_attr(runtime, "work_dir")
    config = runtime_attr(runtime, "config", RepoMemoryConfig()) or RepoMemoryConfig()
    if not repo or store is None or backend is None or not work_dir:
        return None

    focus_paths = list(state.get("focus_paths", []))
    dirty_paths = list(state.get("dirty_paths", set()))
    dirty_unknown = bool(state.get("dirty_unknown", False))
    changed_paths = list(dict.fromkeys(dirty_paths))
    if dirty_unknown:
        changed_paths = _merge_changed_paths(
            changed_paths,
            _detect_changed_paths_from_backend(backend, work_dir, focus_paths),
        )
    if not changed_paths:
        return None

    limit = config.parse_dirty_path_limit
    prioritized = [path for path in focus_paths if path in changed_paths]
    tail = [path for path in changed_paths if path not in prioritized]
    ordered_paths = (prioritized + tail)[:limit]
    changed_files = _load_changed_files(backend, work_dir, ordered_paths)
    if not changed_files:
        state["dirty_paths"] = {path for path in dirty_paths if path not in ordered_paths}
        state["dirty_unknown"] = False
        return None
    return repo, store, focus_paths, changed_files, dirty_paths


def flush_runtime_state(state: dict[str, Any], runtime: object) -> list[str]:
    prepared = _prepare_flush(state, runtime)
    if prepared is None:
        return []
    repo, store, focus_paths, changed_files, dirty_paths = prepared
    if hasattr(store, "allocate_observed_seq"):
        observed_seq = store.allocate_observed_seq(repo)
    else:
        observed_seq = store.get_sync_state(repo).get("last_observed_seq", 0) + 1
    coordinator = FlushCoordinator(repo=repo, store=store)
    flushed = coordinator.flush(
        changed_files=changed_files,
        observed_seq=observed_seq,
        focus_paths=focus_paths,
    )
    state["dirty_paths"] = {path for path in dirty_paths if path not in flushed}
    state["dirty_unknown"] = False
    state["last_compiled_seq"] = store.get_sync_state(repo).get("last_compiled_seq", observed_seq)
    return flushed


async def aflush_runtime_state(state: dict[str, Any], runtime: object) -> list[str]:
    """Async sibling of :func:`flush_runtime_state`.

    DB writes go through the store's ``a*`` siblings so the caller's event
    loop yields. Sandbox / parsing / embedding work stays sync — those don't
    touch asyncpg.
    """
    prepared = _prepare_flush(state, runtime)
    if prepared is None:
        return []
    repo, store, focus_paths, changed_files, dirty_paths = prepared
    if hasattr(store, "aallocate_observed_seq"):
        observed_seq = await store.aallocate_observed_seq(repo)
    elif hasattr(store, "allocate_observed_seq"):
        observed_seq = store.allocate_observed_seq(repo)
    else:
        observed_seq = store.get_sync_state(repo).get("last_observed_seq", 0) + 1
    coordinator = FlushCoordinator(repo=repo, store=store)
    flushed = await coordinator.aflush(
        changed_files=changed_files,
        observed_seq=observed_seq,
        focus_paths=focus_paths,
    )
    state["dirty_paths"] = {path for path in dirty_paths if path not in flushed}
    state["dirty_unknown"] = False
    if hasattr(store, "aget_sync_state"):
        sync_state = await store.aget_sync_state(repo)
    else:
        sync_state = store.get_sync_state(repo)
    state["last_compiled_seq"] = sync_state.get("last_compiled_seq", observed_seq)
    return flushed


def _merge_changed_paths(*path_sets: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for paths in path_sets:
        for path in paths:
            if path not in seen:
                seen.add(path)
                merged.append(path)
    return merged


def _detect_changed_paths_from_backend(
    backend: Any,
    work_dir: str,
    focus_paths: list[str] | None = None,
) -> list[str]:
    result = backend.execute(f"cd {shlex.quote(work_dir)} && {_GIT_CHANGED_PATHS_COMMAND}")
    if result.exit_code != 0:
        logger.warning(
            "repo_memory_flush_git_inspect_failed work_dir=%s exit_code=%d output=%.200s",
            work_dir,
            result.exit_code,
            result.output or "",
        )
        return []
    coordinator = FlushCoordinator(repo="unknown", store=_NullStore())
    return coordinator.detect_changed_paths(result.output, focus_paths=focus_paths)


def _load_changed_files(
    backend: Any,
    work_dir: str,
    changed_paths: list[str],
) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for path in changed_paths:
        safe_path = shlex.quote(path)
        result = backend.execute(
            f"cd {shlex.quote(work_dir)} && test -f {safe_path} && cat {safe_path}"
        )
        if result.exit_code == 0:
            loaded[path] = result.output
        else:
            # Path was reported dirty but is now unreadable (deleted, perm
            # denied, sandbox state mismatch). Log it — silently dropping
            # leaves stale data in repo memory because the cache stays warm.
            logger.warning(
                "repo_memory_flush_path_unreadable path=%s exit_code=%d output=%.200s",
                path,
                result.exit_code,
                result.output or "",
            )
    return loaded


def _language_from_path(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith(".ts"):
        return "typescript"
    if path.endswith(".go"):
        return "go"
    if path.endswith(".rs"):
        return "rust"
    return "text"


def _parse_path(repo: str, path: str, content: str, observed_seq: int) -> list:
    if path.endswith(".py"):
        return parse_python_revisions(repo, path, content, observed_seq)
    if path.endswith(".ts"):
        return _parsed_entities_to_revisions(
            repo,
            observed_seq,
            parse_typescript_entities(path, content),
        )
    if path.endswith(".go"):
        return _parsed_entities_to_revisions(
            repo,
            observed_seq,
            parse_go_entities(path, content),
        )
    if path.endswith(".rs"):
        return _parsed_entities_to_revisions(
            repo,
            observed_seq,
            parse_rust_entities(path, content),
        )
    return []


def _parsed_entities_to_revisions(
    repo: str,
    observed_seq: int,
    entities: list[ParsedEntity],
) -> list:
    return [
        entity.to_revision(
            repo=repo,
            observed_seq=observed_seq,
            retrieval_text=build_retrieval_text(entity),
        )
        for entity in entities
    ]


class _NullStore:
    def set_last_compiled_seq(self, repo: str, observed_seq: int) -> None:  # noqa: ARG002
        return None
