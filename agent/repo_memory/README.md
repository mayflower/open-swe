# Repository Memory

Repository memory is a repo-scoped memory layer for Open SWE. It tracks file changes and focus areas during agent execution, extracts code entities from supported languages, records repo decisions and events, compiles a compact "core memory" before model calls, and exposes retrieval/history tools for reuse.

## Current Status

- Wired into `get_agent()` in `agent/server.py`
- Enabled automatically for execution-mode agents when repo metadata is present
- Supports `InMemoryRepoMemoryStore` for local/default execution
- Supports `PostgresRepoMemoryStore` as the durable backend when `REPO_MEMORY_DATABASE_URL` is set
- Supported parsers: Python, TypeScript, Go, Rust
- Current tools:
  - `remember_repo_decision`
  - `search_similar_code`
  - `get_entity_history`

## Installation

There is no separate package to install for repository memory in this fork. Use this branch and the normal project setup:

```bash
git checkout feature/repo-memory
uv sync
make test TEST_FILE=tests/repo_memory/
```

If you want a local Postgres instance for the next persistence step, this repo now includes Docker Compose for Postgres with `pgvector`:

```bash
make postgres-up
```

Default connection string:

```bash
postgresql://open_swe:open_swe@localhost:5432/open_swe
```

The `vector` extension is created automatically on first startup.

Repo memory switches to the Postgres-backed store automatically when `REPO_MEMORY_DATABASE_URL` is set. If it is unset, the runtime stays on the in-memory adapter.

## How To Use It

Repository memory is activated through the normal agent creation path in `agent/server.py`. The key requirement is repo metadata in the agent config so the runtime can scope memory to a repository.

Example config:

```python
config = {
    "configurable": {
        "__is_for_execution__": True,
        "thread_id": "thread-123",
    },
    "metadata": {
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
    },
}
```

When `get_agent(config)` runs:

- a `RepoMemoryRuntime` is attached to `config["metadata"]["repo_memory_runtime"]`
- `config["metadata"]["repo_full_name"]` is set
- repo-memory middleware is added to the Deep Agents middleware list
- repo-memory tools are added to the tool list

At runtime:

- `read_file`, `write_file`, `edit_file`, `grep`, and `execute` update dirty/focus state
- before-model injection resolves the repo-memory runtime from agent metadata and flushes dirty files automatically
- `execute` can mark repo memory dirty through `dirty_execute_exit_codes`, and the next before-model step probes changed paths from git-style name-status output
- repo decisions can be stored as events
- a separate system message with compiled repo memory is injected before model calls
- retrieval tools can search current entities and prior repo events

## Configuration

The current configuration surface is defined by `RepoMemoryConfig` in `agent/repo_memory/config.py`.

Available knobs:

- `backend`
- `database_url`
- `embedding_provider`
- `embedding_dimensions`
- `repo_scope_only`
- `max_core_memory_tokens`
- `core_block_token_budgets`
- `max_event_search_results`
- `max_similarity_results`
- `focus_path_limit`
- `parse_dirty_path_limit`
- `dirty_execute_exit_codes`
- `same_language_bonus`
- `same_kind_bonus`
- `freshness_bonus`

Example custom runtime:

```python
from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.runtime import RepoMemoryRuntime

runtime = RepoMemoryRuntime(
    repo="langchain-ai/open-swe",
    config=RepoMemoryConfig(
        backend="postgres",
        database_url="postgresql://open_swe:open_swe@localhost:5432/open_swe",
        max_similarity_results=8,
        parse_dirty_path_limit=40,
    ),
)
```

The default server wiring now reads `REPO_MEMORY_BACKEND`, `REPO_MEMORY_DATABASE_URL`, `REPO_MEMORY_EMBEDDING_PROVIDER`, and `REPO_MEMORY_EMBEDDING_DIMENSIONS` through `RepoMemoryConfig`.

## Behavior

The current flow is:

1. Tool middleware updates `dirty_paths`, `focus_paths`, and `focus_entities`.
2. Before-model middleware resolves the runtime, probes git-style changed paths when `dirty_unknown` is set, and flushes bounded dirty files into repo memory before compiling context.
3. Event memory stores append-only repo events such as design decisions.
4. The flush path routes parsed files and entities into either the in-memory adapter or the Postgres-backed store, depending on runtime config.
5. Before-model middleware compiles core memory blocks and injects them as a separate system message.
6. Retrieval tools search current entities and repo history without mutating exact tool outputs. On the Postgres path, entity retrieval uses persisted pgvector embeddings; on the in-memory path, it falls back to lexical ranking.

## Testing

Repo-memory coverage lives under `tests/repo_memory/`.

Run the focused suite with:

```bash
make test TEST_FILE=tests/repo_memory/
```

Key smoke tests:

- `tests/repo_memory/test_end_to_end_repo_memory.py`
- `tests/repo_memory/test_agent_wiring.py`

## Limitations

- The in-memory adapter is still the default when no database URL is configured.
- The Postgres schema is created lazily by the adapter rather than through a separate migration system.
- The default embedding provider is deterministic local hashing; external embedding services are not wired yet.
- Docker-backed validation depends on a running local Docker daemon.
- Git provenance is best-effort and deep history is still lightweight.
- Full sandbox harness e2e is not verified beyond the focused repo-memory tests.
- The current implementation is wired through the existing Open SWE server path, not a standalone subsystem.
