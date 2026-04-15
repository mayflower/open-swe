# GREEN — end-to-end persisted RAG flow

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **GREEN**  
Current slice goal: **Make the new persisted end-to-end repo-memory flow test pass with the minimum production changes.**

## Prerequisites

- 13_red_end_to_end_auto_refresh_flow

## Likely files to touch

- `agent/server.py`
- runtime / injection / sync files
- retrieval wiring
- `tests/repo_memory/test_end_to_end_persisted_rag.py`

## Tests or checks for this slice

- the new persisted flow test passes
- earlier persistence, retrieval, and durable sync tests remain green
- existing agent wiring smoke tests remain green

## Notes and boundaries

- Do not re-architect the entire server wiring.
- Prefer the smallest real-path change that closes the remaining gap.
- Keep the change compatible with the earlier runtime, persistence, retrieval, and durable-sync seams.

## What to do

1. Implement only the missing production behavior needed to make the new persisted-flow test pass.
2. If a small server or runtime wiring change is necessary, keep it narrow and well covered by tests.
3. Run the new end-to-end test first, then the focused repo-memory suite or narrowest broad target that proves no regressions.
4. Stop once the slice is green.
