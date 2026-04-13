# Repository Memory

Repository memory is a repo-scoped memory layer for Open SWE. It tracks file changes and focus areas during agent execution, extracts code entities from supported languages, records repo decisions and events, compiles a compact "core memory" before model calls, and exposes retrieval/history tools for reuse.

## Current Status

- Wired into `get_agent()` in `agent/server.py`
- Enabled automatically for execution-mode agents when repo metadata is present
- Current persistence is in-memory only via `InMemoryRepoMemoryStore`
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
- changed files can be flushed into parsed entities and revisions
- repo decisions can be stored as events
- a separate system message with compiled repo memory is injected before model calls
- retrieval tools can search current entities and prior repo events

## Configuration

The current configuration surface is defined by `RepoMemoryConfig` in `agent/repo_memory/config.py`.

Available knobs:

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
        max_similarity_results=8,
        parse_dirty_path_limit=40,
    ),
)
```

Today, this config is not yet wired through env vars or a dedicated config file. If you want non-default behavior, inject a custom `RepoMemoryRuntime` or adjust the server wiring.

## Behavior

The current flow is:

1. Tool middleware updates `dirty_paths`, `focus_paths`, and `focus_entities`.
2. Flush logic parses changed files into file revisions, entity revisions, and current canonical entities.
3. Event memory stores append-only repo events such as design decisions.
4. Before-model middleware compiles core memory blocks and injects them as a separate system message.
5. Retrieval tools search current entities and repo history without mutating exact tool outputs.

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

- Persistence is in-memory only.
- There is no migration or database layer yet.
- There is no external config plumbing yet.
- Full harness e2e is not verified in this sandboxed environment.
- The current implementation is wired through the existing Open SWE server path, not a standalone subsystem.
