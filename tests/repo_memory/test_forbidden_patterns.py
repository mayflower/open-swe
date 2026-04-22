from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_MEMORY_ROOT = Path(__file__).resolve().parents[2] / "agent" / "repo_memory"
TOOLS_DIR = Path(__file__).resolve().parents[2] / "agent" / "tools"

_PRODUCTION_DIRECTORIES = (REPO_MEMORY_ROOT, TOOLS_DIR)

_ENTITY_PARSER_FILES = (
    REPO_MEMORY_ROOT / "parsing" / "python_parser.py",
    REPO_MEMORY_ROOT / "parsing" / "typescript_parser.py",
    REPO_MEMORY_ROOT / "parsing" / "go_parser.py",
    REPO_MEMORY_ROOT / "parsing" / "rust_parser.py",
)

_REGEX_IMPORT_RE = re.compile(r"^\s*import\s+re(\s|$)", re.MULTILINE)
_REGEX_FROM_IMPORT_RE = re.compile(r"^\s*from\s+re\s+import\b", re.MULTILINE)

_FORBIDDEN_MARKERS = (
    "TODO: implement",
    "NotImplementedError",
    "lexical fallback",
    "in-memory production",
    "placeholder deep history",
    "stub implementation",
    "fake similarity",
)

_ALLOWED_MARKER_FILES: dict[str, set[str]] = {
    "NotImplementedError": {
        str(REPO_MEMORY_ROOT / "embeddings.py"),
    },
}


def _iter_production_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _PRODUCTION_DIRECTORIES:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


@pytest.mark.parametrize("parser_path", _ENTITY_PARSER_FILES, ids=lambda p: p.name)
def test_entity_parsers_do_not_use_regex(parser_path: Path) -> None:
    source = parser_path.read_text(encoding="utf-8")
    assert not _REGEX_IMPORT_RE.search(source), (
        f"{parser_path} uses `import re`; entity parsers must rely on Tree-sitter."
    )
    assert not _REGEX_FROM_IMPORT_RE.search(source), (
        f"{parser_path} uses `from re import`; entity parsers must rely on Tree-sitter."
    )


@pytest.mark.parametrize("marker", _FORBIDDEN_MARKERS)
def test_production_code_does_not_contain_soft_markers(marker: str) -> None:
    allowed = _ALLOWED_MARKER_FILES.get(marker, set())
    offenders: list[str] = []
    for file in _iter_production_python_files():
        text = file.read_text(encoding="utf-8")
        if marker in text and str(file) not in allowed:
            offenders.append(str(file))
    assert not offenders, (
        f"Forbidden marker {marker!r} found in production files: {offenders}. "
        "Either remove the placeholder or allowlist the file explicitly."
    )


def test_repositories_module_requires_explicit_backend_opt_in() -> None:
    repositories = (REPO_MEMORY_ROOT / "persistence" / "repositories.py").read_text(
        encoding="utf-8"
    )
    config_module = (REPO_MEMORY_ROOT / "config.py").read_text(encoding="utf-8")

    assert "REPO_MEMORY_DATABASE_URL" in repositories
    # create_repo_memory_store has to reject the default (un-opted-in) state; the
    # guard is expressed as a sentinel backend value that the factory refuses.
    assert "unconfigured" in config_module, (
        "RepoMemoryConfig.resolved_backend must expose a non-default sentinel so that "
        "create_repo_memory_store can refuse silent in-memory fallbacks in production."
    )
    assert "REPO_MEMORY_ALLOW_IN_MEMORY" in config_module, (
        "RepoMemoryConfig must require REPO_MEMORY_ALLOW_IN_MEMORY opt-in for the "
        "in-memory adapter instead of defaulting to it."
    )
