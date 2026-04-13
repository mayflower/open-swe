# Repo Memory Codex Discovery

This file records the real commands, entrypoints, and integration sites for implementing the
repository-memory prompt pack in this Open-SWE fork.

## Repo Snapshot

- Repo root: `/Users/johann/src/ml/open-swe`
- Python package: `agent`
- Task runner / package manager: `uv`
- Graph entrypoint: `agent.server:get_agent` from `langgraph.json`
- Agent assembly function: `get_agent()` in `agent/server.py`

## Commands

### Canonical test command

```bash
make test
```

`Makefile` resolves this to:

```bash
uv run pytest -vvv tests/
```

### Narrow test command pattern

Use either of these patterns for focused runs:

```bash
make test TEST_FILE=tests/test_repo_extraction.py
```

```bash
uv run pytest -vvv tests/test_repo_extraction.py
```

Future repo-memory slices should likely use:

```bash
make test TEST_FILE=tests/repo_memory/
```

### Integration test command

```bash
make integration_tests
```

This runs:

```bash
uv run pytest -vvv tests/integration_tests/
```

The directory does not currently exist, so the target is guarded and may no-op.

### Lint command

```bash
make lint
```

This runs:

```bash
uv run ruff check .
uv run ruff format . --diff
```

### Format command

```bash
make format
```

This runs:

```bash
uv run ruff format .
uv run ruff check --fix .
```

## Integration Points

### Agent assembly

- File: `agent/server.py`
- Function: `async def get_agent(config: RunnableConfig) -> Pregel`
- The active graph entry in `langgraph.json` points to `agent.server:get_agent`

### Tool registration site

The custom tool list is passed directly to `create_deep_agent(...)` inside `get_agent()` in
`agent/server.py`.

Current registration is an inline `tools=[...]` list, not a separate registry module.

### Middleware registration site

The middleware stack is passed directly to `create_deep_agent(...)` inside `get_agent()` in
`agent/server.py`.

Current middleware exports come from `agent/middleware/__init__.py`:

- `ToolErrorMiddleware`
- `check_message_queue_before_model`
- `ensure_no_empty_msg`
- `open_pr_if_needed`

### Prompt / system context site

- `agent/prompt.py` builds the system prompt via `construct_system_prompt(...)`
- `get_agent()` calls `construct_system_prompt(...)` before creating the agent

## Persistence / Migration Facts

- No existing SQLAlchemy, SQLModel, Alembic, or migration framework was found in this repo.
- Current persistent cross-run state appears to rely on LangGraph thread/store usage in
  `agent/webapp.py`, not on an application-owned relational schema.
- For the repo-memory prompts, persistence should be introduced carefully and kept separate from
  sandbox execution and tool-output shaping.

## Recommended Placement For This Feature

Unless a later slice finds a better existing pattern, place new code here:

- `agent/repo_memory/`
- `agent/repo_memory/parsing/`
- `agent/repo_memory/persistence/`
- `agent/repo_memory/provenance/`
- `agent/repo_memory/retrieval/`
- `agent/repo_memory/middleware/`
- `agent/tools/`

Place new tests here:

- `tests/repo_memory/`

This repo currently keeps most tests flat under `tests/`, so `tests/repo_memory/` would be a new
subdirectory rather than an existing pattern.

## Fork-Specific Notes

- This fork does not currently have a repo-root `AGENTS.md`; bootstrap adds one.
- Tool and middleware wiring are both centralized in `agent/server.py`, which matches the pack's
  assumption to extend the existing harness instead of creating a second entrypoint.
- The repo already uses `uv`, `pytest`, and `ruff`; later slices should follow those commands
  instead of inventing alternate scripts.

## Discovery Commands Used

The bootstrap pass inspected the repo with:

```bash
rg --files /Users/johann/src/ml/open-swe
sed -n '1,260p' open-swe-repo-memory-codex-pack/PROMPTS/00_start_here_bootstrap.md
sed -n '1,260p' pyproject.toml
sed -n '1,260p' Makefile
sed -n '1,320p' README.md
sed -n '180,340p' agent/server.py
sed -n '1,220p' agent/middleware/__init__.py
sed -n '1,240p' langgraph.json
rg -n "alembic|sqlmodel|sqlalchemy|migrat|Base.metadata|create_engine|Session|store\\.put_item|store\\.search|sqlite|postgres" .
```
