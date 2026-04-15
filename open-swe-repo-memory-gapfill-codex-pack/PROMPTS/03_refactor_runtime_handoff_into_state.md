# REFACTOR — Postgres runtime and store wiring

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

Current prompt type: **REFACTOR**  
Current slice goal: **Clean up Postgres runtime-resolution code without changing behavior.**

## Prerequisites

- 02_green_runtime_handoff_into_state

## Likely files to touch

- `agent/repo_memory/runtime.py`
- `agent/server.py`
- `tests/repo_memory/test_runtime_postgres_wiring.py`
- `docs/repo_memory_gapfill_discovery.md`

## Tests or checks for this slice

- runtime/store wiring tests remain green
- agent wiring tests remain green

## Notes and boundaries

- Do not start schema implementation in this slice.
- Keep any helper extraction small and local.

## Rules specific to REFACTOR

- Keep behavior unchanged.
- Keep the same tests green.
- Remove duplication, tighten helper boundaries, and improve names.

## What to do

1. Refactor only for clarity and duplication reduction.
2. Keep the same tests green.
3. Update the discovery doc only if the implementation seam changed materially.
