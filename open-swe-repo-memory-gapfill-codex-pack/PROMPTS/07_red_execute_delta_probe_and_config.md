# RED — embeddings and pgvector retrieval

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **RED**  
Current slice goal: **Write failing tests that prove persisted entities can be embedded, stored with vectors, and retrieved through pgvector-backed similarity search.**

## Prerequisites

- 06_refactor_before_model_flush_and_sync

## Likely files to touch

- `tests/repo_memory/test_pgvector_retrieval.py`
- retrieval/search modules
- persistence model / adapter files
- any embedding-provider seam

## Tests or checks for this slice

- persisted entities can carry embedding/vector state
- retrieval can issue a vector-backed nearest-neighbor query
- tests can use deterministic fake embeddings
- lexical fallback, if present, is explicit and not silently confused with vector retrieval

## Notes and boundaries

- Do not widen into dirty refresh in this slice.
- Avoid real embedding API calls in unit tests.
- Keep one clear failure reason per test.

## What to do

1. Read the current retrieval code and any persistence/search tests.
2. Add failing tests for pgvector-backed similarity behavior.
3. Run the narrowest tests and stop with the expected failing signal.
