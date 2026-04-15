# Verified Gaps

This pack is based on a verification pass over the branch implementation.

## 1. Repo memory is not durably persisted

Observed shape:
- repo memory uses `InMemoryRepoMemoryStore`
- runtime wiring creates in-process store instances
- README and code path do not prove persistence across process lifetime

Implication:
- repo-memory state disappears on restart, so this is not yet durable RAG.

## 2. There is no canonical Postgres adapter path

Observed shape:
- there is no proven repository adapter that makes Postgres the source of truth for repo-memory state
- current persistence tests are in-memory-oriented
- Docker Compose for Postgres/pgvector may exist, but repo-memory does not clearly depend on it

Implication:
- the branch lacks the durable repository layer needed for real storage-backed retrieval.

## 3. Retrieval is lexical/MVP rather than vector-backed

Observed shape:
- similarity search is lexical/MVP today
- there is no proven embedding pipeline
- there is no proven pgvector query path

Implication:
- current retrieval is useful for MVP reuse, but it is not the intended Postgres/pgvector RAG path.

## 4. Dirty refresh does not clearly update durable retrieval state

Observed shape:
- dirty/focus tracking exists
- flush/sync code exists
- there is no proven path that writes refreshed entities and retrieval artifacts into Postgres

Implication:
- even if auto-refresh works in-process, it does not yet guarantee durable RAG freshness.

## 5. End-to-end coverage does not prove durable storage and retrieval

Observed shape:
- smoke tests prove in-process automatic flow
- they do not prove persistence across a database boundary
- they do not prove vector-backed retrieval

Implication:
- the current tests do not yet establish that repo memory behaves like a real Postgres RAG subsystem.

## Non-core but relevant follow-up gaps

These are useful, but not the main line of this pack:
- embedding-provider selection and cost controls may need a follow-up pass
- provenance/deep-history remains lightweight
- production observability and reindex tooling may remain incomplete
