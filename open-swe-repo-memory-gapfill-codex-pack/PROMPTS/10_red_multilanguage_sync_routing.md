# RED — durable refresh and multi-language sync

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **RED**  
Current slice goal: **Write failing tests that prove dirty refresh persists updated Python, TypeScript, Go, and Rust entities into Postgres and updates retrieval state.**

## Prerequisites

- 09_refactor_execute_delta_probe_and_config

## Likely files to touch

- `tests/repo_memory/test_durable_sync.py`
- sync / injection / dirty-tracking files
- parser modules if current tests need an adapter seam

## Tests or checks for this slice

- flushing a dirty `.py`, `.ts`, `.go`, or `.rs` file updates durable canonical entity state
- vector/index state is refreshed consistently for updated entities
- unsupported extensions still no-op cleanly
- dirty state is cleared or normalized after a successful durable flush

## Notes and boundaries

- Do not replace the parser approach in this slice.
- Reuse the existing parser modules and their current output shapes.
- The tests should target the durable sync path, not just parser modules in isolation.

## What to do

1. Read the current sync path, parser modules, and any existing parser tests.
2. Add failing tests that prove the refresh path must land in Postgres and update retrieval state.
3. Use tiny representative fixtures for each language.
4. Run the narrowest tests and stop once they fail for the expected reason.
