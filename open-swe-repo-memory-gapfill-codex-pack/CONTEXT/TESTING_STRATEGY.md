# Testing Strategy

This pack is deliberately test-first.

## RED
Write or update tests that describe the missing behavior.

Rules:
- add tests only
- if needed, add the tiniest compile-time scaffold that raises `NotImplementedError`
- do not implement feature behavior
- run the narrowest target that proves the gap exists

## GREEN
Implement the smallest change that makes the RED tests pass.

Rules:
- prefer the existing architecture and seams
- avoid speculative abstractions
- do not widen the implementation to future slices
- run the narrow test target first, then a slightly broader target

## REFACTOR
Improve structure without changing behavior.

Rules:
- keep the same assertions green
- reduce duplication
- improve helper boundaries
- update small nearby docs and types if needed
- do not change product scope

## Test design preferences

- Prefer deterministic unit tests with small fixtures and fakes.
- Use fake stores, fake embeddings, and fake content loaders where possible.
- Use integration-style tests where the database or retrieval boundary is the actual gap being closed.
- Prefer local Postgres/pgvector validation through the repo's Docker Compose setup for end-to-end checks.
- Keep one clear failure reason per new RED test.
- If a test is trying to prove multiple gaps at once, split it.

## Final validation

The final prompt should:
- run the focused repo-memory suite
- run any narrow smoke/integration tests added in this pack
- validate the Postgres/pgvector path if practical in the local environment
- update the README so it matches what the tests now prove
