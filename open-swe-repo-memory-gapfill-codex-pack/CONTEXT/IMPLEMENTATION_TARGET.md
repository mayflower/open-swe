# Implementation Target

The objective of this pack is to make repo memory **durable, queryable, and behaviorally honest** as a Postgres-backed RAG subsystem.

## Definition of done

A good outcome for this pack looks like this:

1. **Runtime wiring resolves a durable store**
   - repo-memory middleware can resolve a runtime in real execution paths
   - that runtime points at a Postgres-backed repository adapter rather than in-process-only state
   - tests do not need to manually substitute a fake runtime just to reach persistence code

2. **Canonical storage lives in Postgres**
   - repo files, entity revisions, repo events, core blocks, and sync state are stored durably
   - repository adapters can read the latest canonical view and historical rows consistently
   - process restart does not erase repo-memory state

3. **RAG retrieval is pgvector-backed**
   - persisted entities have embeddings or vector-ready rows
   - retrieval can execute similarity search against pgvector
   - tests can prove ranking behavior with deterministic fake embeddings where needed

4. **Dirty refresh updates durable state**
   - if files were read/edited/written or touched by `execute`, the before-model path resolves and flushes them
   - the durable store is fresh before repo-memory blocks are compiled
   - indexing state is updated consistently after a successful flush

5. **Sync routes all existing language parsers**
   - Python continues to work
   - TypeScript, Go, and Rust are routed through the same durable refresh path
   - unsupported files still fail closed / no-op cleanly

6. **The end-to-end flow is proven with a realistic test**
   - the test should use middleware state updates and before-model logic
   - it should persist data into Postgres
   - it should retrieve durable repo-memory context through the real retrieval path

7. **README truthfulness improves**
   - the README should describe what is now automatic
   - it should describe what is durable, what is vector-backed, and what remains limited or stubbed
   - it should avoid implying more completeness than the tests prove

## What is explicitly out of scope here

- distributed indexing infrastructure
- cross-repo memory
- tree-sitter migration
- deep git-history reconstruction
- production-grade embedding job orchestration
- a full relations graph
