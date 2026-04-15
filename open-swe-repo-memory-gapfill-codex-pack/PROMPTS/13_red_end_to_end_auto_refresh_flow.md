# RED — end-to-end persisted RAG flow

## Paste this into Codex

You are closing Postgres RAG gaps in the repo-memory implementation on this Open-SWE branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read the pack context files.
3. Read `docs/repo_memory_gapfill_discovery.md`.
4. If discovery is missing, stop and run `00_start_here_bootstrap_and_baseline.md`.

Current prompt type: **RED**  
Current slice goal: **Write failing tests that prove the automatic runtime handoff + dirty refresh + persisted retrieval flow works against Postgres/pgvector without manual flush calls.**

## Prerequisites

- 12_refactor_multilanguage_sync_routing

## Likely files to touch

- `tests/repo_memory/test_end_to_end_persisted_rag.py`
- `tests/repo_memory/test_end_to_end_repo_memory.py`
- runtime / injection / sync files

## Tests or checks for this slice

- a realistic flow can start with normal state creation, observe tool calls, and reach injected repo memory without manual state seeding
- the flow does not manually call flush; the middleware path performs the refresh
- refreshed entities and events are persisted to Postgres
- retrieval can read persisted repo-memory context through the real retrieval path

## Notes and boundaries

- Keep this test as realistic as possible, but do not require a full production harness boot.
- Reuse the seams built in earlier slices rather than bypassing them.
- The goal is to prove durable storage and retrieval, not to test every single branch of the repo-memory system.

## What to do

1. Read the current smoke test and all earlier slice tests.
2. Add a failing end-to-end style test that uses the automatic persisted flow.
3. Keep the fixture small and focused on one or two files/entities.
4. Run the narrowest new test and stop once it fails for the right reason.
