# REFACTOR — durable refresh and multi-language sync

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **REFACTOR**  
Current slice goal: **Clean up durable sync routing and indexing updates without changing behavior.**

## Prerequisites

- 11_green_multilanguage_sync_routing

## Likely files to touch

- `agent/repo_memory/sync.py`
- parser conversion helpers
- `tests/repo_memory/test_durable_sync.py`

## Tests or checks for this slice

- durable sync tests remain green
- existing parser tests remain green
- pgvector retrieval tests remain green

## Notes and boundaries

- Do not widen into new languages or new parser infrastructure.
- Keep behavior unchanged.

## What to do

1. Refactor local helpers, naming, and conversion logic for readability.
2. Keep the same tests green.
3. Stop when the routing code is simpler and still well covered.
