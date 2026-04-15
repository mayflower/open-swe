# Open-SWE Repo Memory Postgres RAG Codex Prompt Pack

This pack is a **test-driven, red -> green -> refactor** prompt sequence for turning the current
repo-memory branch into a **durable Postgres-backed RAG system**.

It is not a generic repo-memory cleanup pack. It is specifically for replacing the current
in-memory-only behavior with a real persistence and retrieval architecture that uses:

- Postgres as the canonical store
- `pgvector` for embedding-backed retrieval
- the existing Docker Compose setup for local validation

## Targeted gaps

The pack is built around five architectural gaps:

1. **Persistence gap**
   - repo memory currently lives in an in-process `InMemoryRepoMemoryStore`
   - a process restart loses files, entities, events, core blocks, and sync state

2. **RAG storage gap**
   - there is no canonical Postgres schema for repo files, entity revisions, repo events, or sync state
   - there is no repository adapter that makes Postgres the source of truth

3. **Vector retrieval gap**
   - retrieval is lexical/MVP today
   - there is no embedding pipeline, vector column, or pgvector-backed nearest-neighbor lookup

4. **Refresh/indexing gap**
   - dirty tracking and flush logic exist
   - but there is no proven path that persists refreshed entities into Postgres and updates retrieval vectors

5. **End-to-end proof gap**
   - current smoke coverage proves in-process automatic refresh
   - it does not prove durable storage and RAG retrieval across a persistence boundary

## What this pack changes

The prompts drive Codex to:

- discover the real branch shape first, including Docker Compose and persistence code
- add failing tests for durable Postgres storage and retrieval behavior
- implement the minimum schema, adapters, indexing, and runtime wiring needed to pass those tests
- keep retrieval and persistence honest in the README
- finish with validation against the local Postgres/pgvector stack

## What this pack does **not** do

It does not ask Codex to:

- replace the existing Open-SWE harness
- redesign parsing around tree-sitter
- build a full git-history engine
- add cross-repo knowledge graphs
- build distributed indexing infrastructure

Those are follow-up tracks. This pack is for **making repo memory durable and RAG-capable first**.

## Why the pack is Codex-oriented

The pack includes an `AGENTS.md.template`, a discovery-first bootstrap prompt, and small incremental
prompts because Codex works best with:

- a clear project root
- a small verifiable slice per prompt
- explicit red/green/refactor boundaries
- concrete stop conditions

## How to use

1. Unzip this pack into the repository root.
2. Copy `AGENTS.md.template` to `AGENTS.md` if the repo does not already have one, or merge the relevant rules.
3. Start Codex from the repository root.
4. Paste `PROMPTS/00_start_here_bootstrap_and_baseline.md`.
5. Continue in order. Do not skip red/green/refactor gates.
6. After each prompt, update `TRACKER.md`.
7. If Codex finds that the real code differs from the assumptions here, it should follow the discovery doc, not this README.

## Pack layout

- `AGENTS.md.template`
- `README.md`
- `RUNBOOK.md`
- `PROMPT_INDEX.md`
- `TRACKER.md`
- `manifest.json`
- `CONTEXT/VERIFIED_GAPS.md`
- `CONTEXT/IMPLEMENTATION_TARGET.md`
- `CONTEXT/FILES_OF_INTEREST.md`
- `CONTEXT/TESTING_STRATEGY.md`
- `PROMPTS/*.md`

## Recommended workflow discipline

For every **RED** prompt:
- add or update tests only
- run the narrowest relevant tests
- stop with a failing signal

For every **GREEN** prompt:
- make the failing tests pass with the minimum viable implementation
- run the narrow test target, then a slightly broader target
- stop once the slice is green

For every **REFACTOR** prompt:
- improve structure without changing behavior
- keep the same tests green
- clean docs and helpers only inside the current slice

## Expected end state

At the end of this pack, the branch should prove all of the following:

- repo memory persists to Postgres instead of only process memory
- dirty refresh updates durable repo-memory state before injection
- retrieval can use pgvector-backed similarity search over persisted entities
- sync routes the existing Python, TypeScript, Go, and Rust extraction paths into the durable store
- a realistic end-to-end test proves durable storage and retrieval across the runtime boundary
- the README describes what is real, what remains stubbed, and what is still limited
