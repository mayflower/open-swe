# FINAL — validation and release notes

## Paste this into Codex

You are finishing the repo-memory Postgres RAG pack.

Before you do anything:
1. Read `AGENTS.md` at repo root if present.
2. Read `docs/repo_memory_gapfill_discovery.md`.
3. Read `docs/repo_memory_gapfill_baseline.md`.
4. Read `open-swe-repo-memory-gapfill-codex-pack/CONTEXT/IMPLEMENTATION_TARGET.md`.
5. Read the updated `agent/repo_memory/README.md`.

## Goal

Validate that the implemented slices are green together and produce a concise release-ready summary of what is now true.

## What to run

Use the exact real test commands recorded in the discovery doc. At minimum:
- the focused repo-memory tests touched by this pack
- the new or updated Postgres / persistence / retrieval tests
- the new or updated end-to-end / smoke tests
- the local Docker Compose Postgres path if practical in the environment

If the full focused suite is practical, run it. If not, run the narrowest set that still gives confidence and report the gap.

## What to produce

Create or update:
- `docs/repo_memory_gapfill_release_notes.md`

That file should contain:
- what changed
- what is now proven by tests
- what still remains limited
- any follow-up work you would recommend next

## Validation checklist

Confirm whether each is now true:
- runtime wiring resolves a Postgres-backed store
- canonical repo-memory state persists durably
- retrieval can use pgvector-backed similarity search
- dirty refresh updates durable storage and retrieval state
- sync routes `.py`, `.ts`, `.go`, and `.rs` through the durable path
- smoke/end-to-end tests prove the automatic persisted flow
- README matches current reality

## Rules

- Do not add new product behavior in this prompt.
- Only fix tiny, clearly necessary validation issues if a test reveals one.
- If you have to make a tiny fix, rerun the relevant tests and report it clearly.

## Stop condition

Stop only when:
- the relevant tests were run,
- `docs/repo_memory_gapfill_release_notes.md` exists,
- and you can clearly state what is green and what still remains limited.
