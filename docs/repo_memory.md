# Repository Memory

This fork now includes a repository-memory scaffold under `agent/repo_memory/`.

## What it adds

- A repo-scoped domain model for files, entities, revisions, events, and core-memory blocks
- An in-memory persistence layer for current-view state plus append-only history
- Parser adapters for Python, TypeScript, Go, and Rust fixtures
- Dirty-tracking and repo-memory injection middleware
- Tools for:
  - `remember_repo_decision`
  - `search_similar_code`
  - `get_entity_history`

## Integration points

- Tools are registered in `agent/server.py`
- Middleware is registered in the existing `create_deep_agent(...)` middleware list
- Repo memory is injected as a separate message block, not by mutating exact tool outputs

## Current implementation notes

- Persistence is currently backed by an in-memory store (`InMemoryRepoMemoryStore`)
- The middleware/tool wiring uses repo metadata from thread config when available
- Flush and injection paths emit lightweight logs for dirty counts and injection size

## Test coverage

The feature coverage lives under `tests/repo_memory/` and includes:

- domain and config contracts
- persistence behavior
- parsing across supported languages
- dirty tracking and flush coordination
- event memory and decision capture
- core memory compilation and injection
- reuse retrieval
- provenance and entity history
- refactor matching and lineage
- end-to-end repo-memory smoke coverage
