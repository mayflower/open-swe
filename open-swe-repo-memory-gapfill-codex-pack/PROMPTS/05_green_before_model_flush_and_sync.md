# GREEN — durable schema and persistence adapters

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **GREEN**  
Current slice goal: **Implement the minimum schema and repository-adapter behavior that makes the new Postgres persistence tests pass.**

## Prerequisites

- 04_red_before_model_flush_and_sync

## Likely files to touch

- persistence model / adapter files
- migration or schema helpers
- `tests/repo_memory/test_postgres_repository.py`

## Tests or checks for this slice

- new persistence tests pass
- runtime/store wiring tests still pass
- any existing persistence-schema tests still pass

## Notes and boundaries

- Do not implement vector retrieval in this slice.
- Keep schema and adapter changes narrow and explicit.
- Prefer the repo's existing Docker Compose / DB conventions over inventing new ones.

## Rules specific to GREEN

- Implement the minimum production behavior that makes the current RED tests pass.
- Do not widen scope into future slices.

## What to do

1. Implement the smallest schema and adapter change that satisfies the persistence tests.
2. Keep the adapter seam easy to reuse in later retrieval and sync slices.
3. Run the failing tests first, then the narrow related repo-memory tests.
4. Stop once the slice is green.
