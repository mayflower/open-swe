# GREEN — Postgres runtime and store wiring

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/VERIFIED_GAPS.md`.
3. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/IMPLEMENTATION_TARGET.md`.
4. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/FILES_OF_INTEREST.md`.
5. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/TESTING_STRATEGY.md`.
6. Read `docs/repo_memory_gapfill_discovery.md` and use the real commands and real paths recorded there.
7. If the discovery file is missing, stop and run `00_start_here_bootstrap_and_baseline.md` first.
8. If the repo layout differs from the likely files below, search by real symbol and adapt.

Current prompt type: **GREEN**  
Current slice goal: **Implement the minimum Postgres-backed runtime/store wiring that makes the new runtime tests pass.**

## Prerequisites

- 01_red_runtime_handoff_into_state

## Likely files to touch

- `agent/repo_memory/runtime.py`
- `agent/server.py`
- `agent/repo_memory/state.py`
- `tests/repo_memory/test_runtime_postgres_wiring.py`

## Tests or checks for this slice

- the runtime wiring tests from the RED slice now pass
- existing agent wiring tests still pass
- the implementation uses a persistence-backed runtime seam rather than hardcoding in-memory storage

## Notes and boundaries

- Do not implement schema migrations in this slice.
- Do not widen into retrieval or embeddings in this slice.
- Prefer a narrow helper such as `resolve_repo_memory_runtime(...)` or a small store-factory seam if the framework requires it.

## Rules specific to GREEN

- Implement the minimum production behavior that makes the current RED tests pass.
- Do not widen scope into future slices.
- Prefer the existing code paths over new abstraction layers.

## What to do

1. Implement the smallest production change that satisfies the new Postgres runtime-resolution tests.
2. Keep the persistence seam explicit and easy to reuse in later slices.
3. Run the failing tests first, then rerun the narrow related repo-memory tests.
4. Stop once the slice is green.
