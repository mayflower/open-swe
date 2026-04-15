from agent.repo_memory.persistence.postgres import PostgresRepoMemoryStore
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.sync import FlushCoordinator


def test_flush_reparses_only_dirty_files_and_prioritizes_focus_paths() -> None:
    store = InMemoryRepoMemoryStore()
    coordinator = FlushCoordinator(repo="repo", store=store)
    changed_files = {
        "agent/other.py": "def alpha():\n    return 1\n",
        "agent/focused.py": "def beta():\n    return 2\n",
    }

    flushed = coordinator.flush(
        changed_files=changed_files,
        observed_seq=4,
        focus_paths=["agent/focused.py"],
    )

    assert flushed == ["agent/focused.py", "agent/other.py"]
    assert store.get_sync_state("repo")["last_compiled_seq"] == 4
    assert store.get_file("repo", "agent/focused.py") is not None


def test_flush_parses_supported_languages_in_live_sync_path() -> None:
    store = InMemoryRepoMemoryStore()
    coordinator = FlushCoordinator(repo="repo", store=store)

    coordinator.flush(
        changed_files={
            "agent/widget.ts": "export class WidgetService {\n  render() {\n    return 1;\n  }\n}\n",
            "agent/widget.go": "type WidgetService struct{}\nfunc (w *WidgetService) Render(value string) {}\n",
            "agent/widget.rs": "pub trait Renderer {}\npub fn helper(value: &str) {}\n",
        },
        observed_seq=5,
        focus_paths=[],
    )

    assert store.get_entity("agent/widget.ts:WidgetService") is not None
    assert store.get_entity("agent/widget.go:WidgetService.Render") is not None
    assert store.get_entity("agent/widget.rs:helper") is not None


def test_flush_persists_typescript_go_and_rust_entities_in_postgres(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    coordinator = FlushCoordinator(repo="repo", store=postgres_store)

    flushed = coordinator.flush(
        changed_files={
            "agent/widget.ts": "export class WidgetService {\n  render() {\n    return 1;\n  }\n}\n",
            "agent/widget.go": "type WidgetService struct{}\nfunc (w *WidgetService) Render(value string) {}\n",
            "agent/widget.rs": "pub trait Renderer {}\npub fn helper(value: &str) {}\n",
        },
        observed_seq=5,
        focus_paths=[],
    )

    assert flushed == ["agent/widget.ts", "agent/widget.go", "agent/widget.rs"]
    assert postgres_store.get_sync_state("repo")["last_compiled_seq"] == 5
    assert postgres_store.get_entity("agent/widget.ts:WidgetService") is not None
    assert postgres_store.get_entity("agent/widget.go:WidgetService.Render") is not None
    assert postgres_store.get_entity("agent/widget.rs:helper") is not None


def test_flush_unsupported_files_fail_closed_in_postgres(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    coordinator = FlushCoordinator(repo="repo", store=postgres_store)

    flushed = coordinator.flush(
        changed_files={"notes/todo.txt": "remember to refactor later\n"},
        observed_seq=6,
        focus_paths=[],
    )

    assert flushed == ["notes/todo.txt"]
    assert postgres_store.get_file("repo", "notes/todo.txt") is not None
    assert list(postgres_store.iter_entities("repo")) == []
    assert postgres_store.get_sync_state("repo")["last_compiled_seq"] == 6
