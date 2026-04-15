# Prompt Index

## 00 — Bootstrap and baseline
Discover the real code layout, Docker Compose/Postgres setup, test commands, and current repo-memory behavior. Write discovery docs.

## 01 / 02 / 03 — Postgres runtime and store wiring
Prove, implement, and clean up the path that makes a Postgres-backed repo-memory runtime available through the real agent wiring.

## 04 / 05 / 06 — Schema and persistence adapters
Prove, implement, and clean up durable schema/repository behavior for files, entities, events, core blocks, and sync state.

## 07 / 08 / 09 — Embeddings and pgvector retrieval
Prove, implement, and clean up embedding generation, vector persistence, and similarity search over pgvector.

## 10 / 11 / 12 — Refresh/index sync into durable storage
Prove, implement, and clean up dirty refresh and multi-language sync so flushed repo memory lands in Postgres and updates retrieval state.

## 13 / 14 / 15 — End-to-end persisted RAG flow
Prove, implement, and clean up a realistic automatic flow test against Postgres/pgvector, then tighten README truthfulness.

## 16 — Final validation and release notes
Run the relevant suites, validate the Docker Compose Postgres path, summarize the remaining limitations, and prepare concise release notes.
