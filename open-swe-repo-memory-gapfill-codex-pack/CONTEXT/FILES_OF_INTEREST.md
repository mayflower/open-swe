# Files of Interest

The discovery prompt should confirm the exact paths on your local checkout, but this is the likely set.

## Entry and integration
- `agent/server.py`
  - wires tools, middleware, sandbox backend, and repo-memory runtime

## Repo-memory runtime and config
- `agent/repo_memory/runtime.py`
- `agent/repo_memory/config.py`
- `agent/repo_memory/state.py`

## Dirty tracking / injection / sync
- `agent/repo_memory/middleware/dirty_tracking.py`
- `agent/repo_memory/middleware/injection.py`
- `agent/repo_memory/delta.py`
- `agent/repo_memory/sync.py`
- `agent/repo_memory/focus.py`

## Parsing
- `agent/repo_memory/parsing/python_parser.py`
- `agent/repo_memory/parsing/typescript_parser.py`
- `agent/repo_memory/parsing/go_parser.py`
- `agent/repo_memory/parsing/rust_parser.py`
- `agent/repo_memory/parsing/common.py`
- any retrieval-text helpers or embedding payload builders

## Persistence / database
- `agent/repo_memory/persistence/repositories.py`
- `agent/repo_memory/persistence/models.py`
- any Postgres adapter or SQL helper modules
- any migration or schema files used by repo memory
- `docker-compose.postgres.yml`
- `docker/postgres/initdb/`

## Retrieval
- any repo-memory retrieval/search modules
- any embedding-provider or vector-query helpers

## Existing tests likely relevant
- `tests/repo_memory/test_agent_wiring.py`
- `tests/repo_memory/test_end_to_end_repo_memory.py`
- persistence schema / repository tests
- dirty tracking tests
- injection/compiler tests
- parser tests for each language
- sync / lineage tests

## Likely new test files in this pack
If the repo has no better existing file for the same purpose, the prompts may create:

- `tests/repo_memory/test_runtime_postgres_wiring.py`
- `tests/repo_memory/test_postgres_repository.py`
- `tests/repo_memory/test_pgvector_retrieval.py`
- `tests/repo_memory/test_durable_sync.py`
- `tests/repo_memory/test_end_to_end_persisted_rag.py`

These are suggestions, not hard requirements.
