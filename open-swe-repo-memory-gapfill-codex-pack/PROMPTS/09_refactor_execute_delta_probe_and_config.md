# REFACTOR — embeddings and pgvector retrieval

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **REFACTOR**  
Current slice goal: **Clean up embedding and pgvector retrieval code without changing behavior.**

## Prerequisites

- 08_green_execute_delta_probe_and_config

## Likely files to touch

- retrieval/search modules
- embedding-provider helpers
- `tests/repo_memory/test_pgvector_retrieval.py`

## Tests or checks for this slice

- pgvector retrieval tests remain green
- persistence tests remain green

## Notes and boundaries

- Do not start durable refresh wiring in this slice.
- Keep refactoring focused on helper extraction, naming, and duplication reduction.

## What to do

1. Refactor only the code introduced or touched by the vector retrieval slice.
2. Keep behavior unchanged.
3. Run the same relevant tests and stop once everything stays green.
