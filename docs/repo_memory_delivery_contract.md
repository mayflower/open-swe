# Repo Memory Delivery Contract

## Non-Negotiables

- Durable repo-memory flows must use Postgres with pgvector enabled.
- Production vector embeddings must come from the configured OpenAI embedding provider.
- The Dreaming service must run as the standalone daemon entrypoint, not as an in-process background thread in the agent server.
- Postgres-backed execution must see an explicitly migrated schema. Missing schema is a startup/runtime error, not a signal to create tables implicitly.

## Required Operator Steps

1. Start the local or shared Postgres harness.
2. Apply repo-memory migrations with `uv run repo-memory-migrate` or `make repo-memory-migrate`.
3. Start the standalone Dreaming daemon if Dreaming consolidation is required.

## Validation Contract

- DB-backed repo-memory tests must use a real Postgres + pgvector database.
- If the local DB harness cannot be reached, the DB-backed test fixture must fail rather than skip.
- Daemon validation must execute the real CLI entrypoint, not just internal helpers.
- End-to-end durable Dreaming validation must exercise an actual pgvector-backed similarity/deduplication path.

## Compatibility Contract

- The durable schema version is tracked in `repo_memory_schema_migrations`.
- Store and daemon startup validate that the latest schema version is present.
- Embedding configuration must match the schema vector width; mismatches are treated as configuration errors.
