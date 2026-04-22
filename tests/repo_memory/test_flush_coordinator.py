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
            "agent/widget.py": "class Widget:\n    def render(self, name):\n        return name\n",
            "agent/widget.ts": "export class WidgetService {\n  render() {\n    return 1;\n  }\n}\n",
            "agent/widget.go": "type WidgetService struct{}\nfunc (w *WidgetService) Render(value string) {}\n",
            "agent/widget.rs": "pub trait Renderer {}\npub fn helper(value: &str) {}\n",
        },
        observed_seq=5,
        focus_paths=[],
    )

    assert store.get_entity("agent/widget.py:Widget") is not None
    assert store.get_entity("agent/widget.py:Widget.render") is not None
    assert store.get_entity("agent/widget.ts:WidgetService") is not None
    assert store.get_entity("agent/widget.go:WidgetService.Render") is not None
    assert store.get_entity("agent/widget.rs:helper") is not None


def test_flush_routes_all_four_languages_through_tree_sitter_dispatch() -> None:
    store = InMemoryRepoMemoryStore()
    coordinator = FlushCoordinator(repo="repo", store=store)

    coordinator.flush(
        changed_files={
            "agent/alpha.py": (
                '"""Alpha module."""\n\nclass Alpha:\n'
                "    def send(self, payload):\n        return payload\n"
            ),
            "agent/beta.ts": (
                "export interface Beta {\n  label: string;\n}\n\n"
                "export class BetaClient {\n  send(x: Beta) { return x.label; }\n}\n"
            ),
            "agent/gamma.go": (
                "package gamma\n\n"
                "type GammaClient struct{}\n\n"
                "func (c *GammaClient) Send(value string) string { return value }\n"
            ),
            "agent/delta.rs": (
                "pub trait DeltaSender { fn send(&self, value: &str); }\n\n"
                "pub struct DeltaClient;\n\n"
                "impl DeltaClient { pub fn send(&self, value: &str) {} }\n"
            ),
        },
        observed_seq=7,
        focus_paths=[],
    )

    assert store.get_entity("agent/alpha.py:Alpha") is not None
    assert store.get_entity("agent/alpha.py:Alpha.send") is not None
    assert store.get_entity("agent/beta.ts:Beta") is not None
    assert store.get_entity("agent/beta.ts:BetaClient.send") is not None
    assert store.get_entity("agent/gamma.go:GammaClient") is not None
    assert store.get_entity("agent/gamma.go:GammaClient.Send") is not None
    assert store.get_entity("agent/delta.rs:DeltaSender") is not None
    assert store.get_entity("agent/delta.rs:DeltaClient.send") is not None


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
