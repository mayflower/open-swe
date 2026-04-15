# REFACTOR — durable schema and persistence adapters

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **REFACTOR**  
Current slice goal: **Tighten the new schema/adapter code without changing behavior.**

## Prerequisites

- 05_green_before_model_flush_and_sync

## Likely files to touch

- persistence model / adapter files
- `tests/repo_memory/test_postgres_repository.py`

## Tests or checks for this slice

- persistence tests remain green
- runtime/store wiring tests remain green

## Notes and boundaries

- Do not start embeddings or retrieval in this slice.
- Keep refactoring local to schema helpers, SQL boundaries, and duplication reduction.

## What to do

1. Refactor helper boundaries, naming, and duplicated SQL or adapter logic.
2. Keep the same tests green.
3. Stop when the persistence code is cleaner and still green.
