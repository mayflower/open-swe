# GREEN — embeddings and pgvector retrieval

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **GREEN**  
Current slice goal: **Implement the minimum embedding/vector retrieval path needed to make the new pgvector tests pass.**

## Prerequisites

- 07_red_execute_delta_probe_and_config

## Likely files to touch

- retrieval/search modules
- persistence model / adapter files
- any embedding-provider seam
- `tests/repo_memory/test_pgvector_retrieval.py`

## Tests or checks for this slice

- new pgvector retrieval tests pass
- persistence tests still pass
- runtime/store wiring tests still pass

## Notes and boundaries

- Keep the embedding provider easy to fake in tests.
- Do not widen into refresh/index orchestration in this slice.
- Prefer a narrow query seam over a full service container.

## What to do

1. Implement the smallest vector retrieval change that satisfies the tests.
2. Persist embeddings or vector-ready rows consistently with the durable schema.
3. Run the failing tests first, then the narrow related repo-memory tests.
4. Stop once the slice is green.
