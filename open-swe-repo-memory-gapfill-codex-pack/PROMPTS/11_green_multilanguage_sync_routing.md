# GREEN — durable refresh and multi-language sync

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **GREEN**  
Current slice goal: **Implement the minimum durable refresh/sync routing needed to make the new persistence-and-index tests pass.**

## Prerequisites

- 10_red_multilanguage_sync_routing

## Likely files to touch

- `agent/repo_memory/sync.py`
- parser adapters or conversion helpers
- persistence / retrieval update helpers
- `tests/repo_memory/test_durable_sync.py`

## Tests or checks for this slice

- new durable sync tests pass
- existing parser tests still pass
- persistence and pgvector retrieval tests still pass

## Notes and boundaries

- Do not broaden the parser feature set beyond current module capabilities.
- Keep the routing code straightforward and centralized.
- Ensure refreshed entities land in durable storage and retrieval state together.

## What to do

1. Implement the smallest sync-path change that reuses the existing parser modules for `.py`, `.ts`, `.go`, and `.rs`.
2. Persist refreshed revisions and update vector/index state consistently.
3. Run the failing tests first, then the nearby parser and repo-memory tests.
4. Stop once the slice is green.
