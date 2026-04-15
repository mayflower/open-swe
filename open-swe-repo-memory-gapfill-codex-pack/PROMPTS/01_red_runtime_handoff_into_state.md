# RED — Postgres runtime and store wiring

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
8. If the repo layout differs from the likely files below, search by real symbol and adapt.

Current prompt type: **RED**  
Current slice goal: **Write failing tests that prove the real runtime wiring can resolve a Postgres-backed repo-memory store without manual test seeding.**

## Prerequisites

- 00_start_here_bootstrap_and_baseline

## Likely files to touch

- `tests/repo_memory/test_runtime_postgres_wiring.py`
- `tests/repo_memory/test_agent_wiring.py`
- `agent/repo_memory/runtime.py`
- `agent/server.py`
- `agent/repo_memory/state.py`

## Tests or checks for this slice

- the normal runtime path can resolve a durable store or repository adapter from real config metadata
- tests do not need to manually construct a fake in-memory runtime to reach the persistence seam
- if no Postgres configuration exists, the path fails clearly or returns `None` as intended by the current design
- the runtime caches the canonical persistence-backed payload back into state for later middleware use

## Notes and boundaries

- Do not implement schema or retrieval behavior in this slice.
- Do not widen into dirty-file flushing in this slice.
- Prefer a small integration-style test around the real runtime/middleware boundary if that is the actual gap.

## Rules specific to RED

- Add or update tests only.
- Do not implement actual feature behavior.
- If the tests need compile-time scaffolding, add the tiniest possible non-behavioral scaffold.
- Prefer one failing reason per test.

## What to do

1. Read the current runtime, server wiring, and state helper.
2. Add failing tests that describe Postgres-backed runtime resolution behavior.
3. Run the narrowest relevant test target and stop once the tests fail for the expected reason.

## Stop condition

Stop only when:
- the work for this slice exists,
- the relevant tests/commands were run,
- and you can report whether the slice is still red or now green as expected.
