# REFACTOR — end-to-end cleanup and README truthfulness

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **REFACTOR**  
Current slice goal: **Tighten the persisted RAG path and update the README so it accurately reflects the now-proven Postgres/pgvector behavior and remaining limitations.**

## Prerequisites

- 14_green_end_to_end_auto_refresh_flow

## Likely files to touch

- `agent/repo_memory/README.md`
- end-to-end repo-memory tests
- runtime / injection / sync / retrieval helpers

## Tests or checks for this slice

- all end-to-end / smoke repo-memory tests remain green
- README describes what is durable now and what still remains limited
- README no longer overstates features that are still stub-level or incomplete

## Notes and boundaries

- Do not add new feature scope in this slice.
- Update docs only to match what tests now prove.
- Preserve the new persisted RAG flow.

## What to do

1. Refactor only where the end-to-end flow or helper names are still awkward.
2. Update `agent/repo_memory/README.md` to match the implementation honestly.
3. Keep the relevant tests green.
4. Stop when the code is cleaner and the README is accurate.
