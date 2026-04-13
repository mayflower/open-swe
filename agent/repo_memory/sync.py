from __future__ import annotations

import logging
from dataclasses import dataclass

from .delta import parse_name_status_diff
from .domain import FileRevision
from .matching import match_entities
from .parsing.python_parser import parse_python_revisions

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FlushCoordinator:
    repo: str
    store: object

    def detect_changed_paths(
        self, diff_output: str, focus_paths: list[str] | None = None
    ) -> list[str]:
        focus_paths = focus_paths or []
        changed = parse_name_status_diff(diff_output)
        prioritized = [path for path in focus_paths if path in changed]
        tail = [path for path in changed if path not in prioritized]
        return prioritized + tail

    def flush(
        self,
        *,
        changed_files: dict[str, str],
        observed_seq: int,
        focus_paths: list[str] | None = None,
    ) -> list[str]:
        focus_paths = focus_paths or []
        ordered_paths = [path for path in focus_paths if path in changed_files] + [
            path for path in changed_files if path not in focus_paths
        ]
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
            old_entities = {
                entity.entity_id: entity
                for entity in self.store.iter_entities(self.repo)
                if entity.path == path
            }
            revisions = _parse_path(self.repo, path, content, observed_seq)
            for revision in revisions:
                best_match = None
                for previous in old_entities.values():
                    decision = match_entities(previous, revision)
                    if best_match is None or decision.confidence > best_match.confidence:
                        best_match = decision
                if best_match and best_match.preserve_identity:
                    revision.entity_id = best_match.old_entity_id
                elif best_match:
                    self.store.record_lineage(
                        revision.entity_id,
                        best_match.old_entity_id,
                        best_match.reason,
                        best_match.confidence,
                    )
                self.store.upsert_entity_revision(revision)
        self.store.set_last_compiled_seq(self.repo, observed_seq)
        logger.info(
            "repo_memory_flush repo=%s dirty_count=%d observed_seq=%d",
            self.repo,
            len(ordered_paths),
            observed_seq,
        )
        return ordered_paths


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
    return []
