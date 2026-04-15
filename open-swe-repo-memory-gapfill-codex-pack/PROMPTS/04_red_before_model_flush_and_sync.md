# RED — durable schema and persistence adapters

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **RED**  
Current slice goal: **Write failing tests that prove repo-memory files, entities, events, core blocks, and sync state can be persisted and read back from Postgres.**

## Prerequisites

- 03_refactor_runtime_handoff_into_state

## Likely files to touch

- `tests/repo_memory/test_postgres_repository.py`
- `tests/repo_memory/test_persistence_schema.py`
- repo-memory persistence model / adapter files

## Tests or checks for this slice

- upserting a file revision persists durable canonical file state
- upserting an entity revision persists durable canonical entity state
- appending repo events persists append-only event history
- core blocks and sync state are stored durably
- restarting the adapter or opening a new session can still read the same data

## Notes and boundaries

- Do not implement embeddings or vector queries in this slice.
- Prefer deterministic database tests over broad harness tests.
- Use the repo's existing local Postgres path if practical; otherwise use adapter-level tests with isolated setup.

## Rules specific to RED

- Add or update tests only.
- Do not implement actual feature behavior.
- Prefer one failing reason per test.

## What to do

1. Read the current persistence models and tests.
2. Add failing tests for durable repository behavior.
3. Run the narrowest relevant tests and stop with the expected failing signal.
