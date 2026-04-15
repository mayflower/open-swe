# START HERE — bootstrap discovery and baseline

## Paste this into Codex

You are preparing to implement durable Postgres-backed repo-memory RAG on this branch.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read `open-swe-repo-memory-gapfill-codex-pack/README.md`.
3. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/VERIFIED_GAPS.md`.
4. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/IMPLEMENTATION_TARGET.md`.
5. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/FILES_OF_INTEREST.md`.
6. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/TESTING_STRATEGY.md`.

## Goal

Discover the **actual** implementation shape in this checkout and record the real paths, symbols, database setup, and test commands so later prompts do not guess.

## What to inspect

At minimum, inspect the real versions of:
- `agent/server.py`
- repo-memory runtime/config/state files
- persistence models and repository adapters
- retrieval/search modules
- dirty tracking, injection, and sync code
- parser modules
- Docker Compose / Postgres / pgvector setup
- existing repo-memory tests

## What to produce

Create or update:
- `docs/repo_memory_gapfill_discovery.md`
- `docs/repo_memory_gapfill_baseline.md`

### `docs/repo_memory_gapfill_discovery.md` must capture

- the real file paths for runtime, persistence, retrieval, sync, delta, and parser modules
- whether `docker-compose.postgres.yml` and pgvector init files already exist
- the real connection string / env var shape for local Postgres if present
- the real tests that already cover repo memory
- the exact command(s) to run focused repo-memory tests in this repo
- the exact command(s) to start and inspect local Postgres/pgvector in this repo
- how repo-memory runtime currently gets created
- whether retrieval is lexical only, vector-backed, or mixed
- any differences between the branch and this pack's assumptions

### `docs/repo_memory_gapfill_baseline.md` must capture

- which focused repo-memory tests pass right now
- which tests are especially relevant to runtime wiring, persistence, retrieval, and durable sync
- whether any local Postgres validation was run
- any immediate blockers that later prompts should know about

## Rules

- Do not make product-behavior changes in this prompt.
- You may add tiny docs or comments only for discovery.
- If you need to run tests, use the narrowest relevant suite first.
- If you need to inspect the local database stack, prefer the repo's existing Docker Compose setup.

## Stop condition

Stop only when:
- both discovery docs exist,
- you ran at least one focused repo-memory test command,
- and the docs record the real commands, real files, and the most relevant baseline facts.

## Expected output from Codex

At the end of this prompt, provide:
- which docs you created or updated
- the exact discovery/test commands you ran
- the most important repo-layout deviations you found
- whether the stop condition is satisfied
