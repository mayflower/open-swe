# Repo Memory Delivery Audit

## Scope

This audit captures the corrected delivery contract for the durable repo-memory + Dreaming path in this branch.

## Current Delivery State

- Canonical durable backend: Postgres + pgvector
- Canonical production embeddings: OpenAI embeddings
- Standalone Dreaming runner: `repo-memory-dreaming-daemon`
- Repo-memory schema management: explicit SQL migration runner via `repo-memory-migrate`
- Online agent behavior: producer/reader only, no in-process Dreaming daemon

## Verified Runtime Shape

- The agent process writes repo-memory events, entities, files, and focus/dirty state into the configured store.
- The standalone Dreaming daemon discovers repos from Postgres, acquires repo-scoped leases, and persists dream runs, claims, claim evidence, and snapshots.
- Before-model injection consumes `snapshot + fresh overlay` when available and falls back to legacy block compilation if Dreaming has not yet produced a snapshot.

## Corrected Reliability Decisions

- Postgres schema creation is no longer embedded inline inside the store implementation.
- The Postgres store validates that the latest repo-memory migration is already applied before it serves queries.
- The Dreaming daemon validates the migrated schema at startup.
- DB-backed test fixtures attempt to start the local Compose Postgres harness and fail hard if that cannot be brought up.

## Remaining Operational Constraints

- Local DB-backed validation still requires a running Docker daemon when no external Postgres is already reachable.
- This repo still does not have a broader application-wide migration framework; repo-memory uses its own explicit SQL migration runner.
- The Dreaming layer still derives claims from persisted repo events rather than a wider trace-ingestion pipeline.
