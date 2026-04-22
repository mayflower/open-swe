# Repository Memory

Repository memory is a repo-scoped memory layer for Open SWE. It tracks file changes and focus areas during agent execution, extracts code entities from supported languages, records repo decisions and events, compiles a compact "core memory" before model calls, and exposes retrieval/history tools for reuse.

The current branch also includes a Dreaming layer on top of repo memory. Dreaming consolidates repo events into durable claims, scores and revalidates them, compiles snapshot blocks with a high-water-mark, and serves before-model context as `snapshot + fresh overlay`.

## Current Status

- Wired into `get_agent()` in `agent/server.py`
- Enabled automatically for execution-mode agents when repo metadata is present
- `PostgresRepoMemoryStore` is the only supported production backend. It is
  required whenever `REPO_MEMORY_DATABASE_URL` is set and `create_repo_memory_store`
  refuses to start otherwise unless `REPO_MEMORY_ALLOW_IN_MEMORY=true` is set
  explicitly (test-only opt-in).
- `InMemoryRepoMemoryStore` exists solely for unit tests. It is not a production
  path and will not be picked up by default wiring in production.
- Standalone Dreaming daemon process that discovers all repos from Postgres.
- Uses OpenAI embeddings as the canonical vector provider for durable retrieval
  and Dreaming claim similarity. A deterministic hashed provider is available
  for tests; it implements the same `EmbeddingProvider` interface so that the
  production pgvector path is exercised end to end.
- Entity parsing for Python, TypeScript, Go, and Rust runs through Tree-sitter
  grammars (`tree-sitter-python`, `tree-sitter-typescript`, `tree-sitter-go`,
  `tree-sitter-rust`). Regex-based entity extraction is not used and is
  enforced by a forbidden-patterns test.
- Current tools:
  - `remember_repo_decision`
  - `search_similar_code` (pgvector when available, `lexical_only` rerank on the
    in-memory adapter)
  - `search_repo_memory` (pgvector over claims + lexical ranking for events)
  - `get_entity_history`

## Installation

There is no separate package to install for repository memory in this fork. Use this branch and the normal project setup:

```bash
git checkout feature/repo-memory
uv sync
make test TEST_FILE=tests/repo_memory/
```

If you want a local Postgres instance for the next persistence step, this repo now includes Docker Compose for Postgres with `pgvector`:

```bash
make postgres-up
make repo-memory-migrate
```

If you also want the standalone Dreaming daemon in Docker:

```bash
make dreaming-up
```

If you switch the embedding contract and need to backfill stored vectors:

```bash
make dreaming-reembed
```

Default connection string:

```bash
postgresql://open_swe:open_swe@localhost:5432/open_swe
```

The Postgres harness enables `pgvector` during init, and repo-memory schema objects are applied by the explicit migration command. The Postgres store validates that the schema is already migrated before serving requests.

Repo memory uses the Postgres-backed store whenever `REPO_MEMORY_DATABASE_URL`
is configured. When neither `REPO_MEMORY_DATABASE_URL` nor
`REPO_MEMORY_ALLOW_IN_MEMORY=true` is set, `create_repo_memory_store` fails fast
with an explicit error; there is no silent in-memory fallback in production.
The durable/vector path is intended to run with
`REPO_MEMORY_EMBEDDING_PROVIDER=openai` and a valid `OPENAI_API_KEY`.

## How To Use It

Repository memory is activated through the normal agent creation path in `agent/server.py`. The key requirement is repo metadata in the agent config so the runtime can scope memory to a repository.

Example config:

```python
config = {
    "configurable": {
        "__is_for_execution__": True,
        "thread_id": "thread-123",
    },
    "metadata": {
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
    },
}
```

When `get_agent(config)` runs:

- a `RepoMemoryRuntime` is attached to `config["metadata"]["repo_memory_runtime"]`
- `config["metadata"]["repo_full_name"]` is set
- repo-memory middleware is added to the Deep Agents middleware list
- repo-memory tools are added to the tool list

At runtime:

- `read_file`, `write_file`, `edit_file`, `grep`, and `execute` update dirty/focus state
- before-model injection resolves the repo-memory runtime from agent metadata and flushes dirty files automatically
- `execute` can mark repo memory dirty through `dirty_execute_exit_codes`, and the next before-model step probes changed paths from git-style name-status output
- repo decisions can be stored as events
- a standalone Dreaming daemon process can continuously fold repo events into claims and snapshots without living inside the agent server
- the standalone Dreaming daemon validates the migrated schema at startup and relies on Postgres leases for repo-scoped single-worker execution
- before-model injection prefers Dreaming snapshots plus a watermark-based fresh overlay, and falls back to legacy block compilation if no snapshot exists yet
- retrieval tools can search current entities and prior repo events

## Configuration

The current configuration surface is defined by `RepoMemoryConfig` in `agent/repo_memory/config.py`.

Available knobs:

- `backend`
- `database_url`
- `embedding_provider`
- `embedding_dimensions`
- `embedding_model`
- `embedding_version`
- `repo_scope_only`
- `max_core_memory_tokens`
- `core_block_token_budgets`
- `max_event_search_results`
- `max_similarity_results`
- `focus_path_limit`
- `parse_dirty_path_limit`
- `dirty_execute_exit_codes`
- `same_language_bonus`
- `same_kind_bonus`
- `freshness_bonus`
- `dreaming_merge_similarity_threshold`
- `dreaming_related_similarity_threshold`
- `dreaming_overlay_similarity_threshold`
- `dreaming_overlay_max_items`
- `dreaming_daemon_poll_interval_seconds`
- `dreaming_daemon_lease_ttl_seconds`

Example custom runtime:

```python
from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.runtime import RepoMemoryRuntime

runtime = RepoMemoryRuntime(
    repo="langchain-ai/open-swe",
    config=RepoMemoryConfig(
        backend="postgres",
        database_url="postgresql://open_swe:open_swe@localhost:5432/open_swe",
        max_similarity_results=8,
        parse_dirty_path_limit=40,
    ),
)
```

The default server wiring now reads `REPO_MEMORY_BACKEND`, `REPO_MEMORY_DATABASE_URL`, `REPO_MEMORY_EMBEDDING_PROVIDER`, `REPO_MEMORY_EMBEDDING_DIMENSIONS`, `REPO_MEMORY_EMBEDDING_MODEL`, and `REPO_MEMORY_EMBEDDING_VERSION` through `RepoMemoryConfig`.

## Dreaming pipeline (OpenClaw-style)

Dreaming now runs as three cooperative phases modelled after
[OpenClaw's Dreaming design](https://docs.openclaw.ai/concepts/dreaming):

- **Light** — ingests new repo events, dedupes candidates via pgvector
  similarity *and* token-set Jaccard (default ≥ 0.9), and attaches evidence to
  the surviving claim. Light never writes a snapshot and never mutates claim
  status.
- **REM** — inspects recent cross-source activity and records a small boost on
  claims that show consolidation signals (≥ 2 distinct runs, threads, or
  paths). REM never mutates status either; it only strengthens signal.
- **Deep** — the only phase that promotes claims and compiles a snapshot.
  Scoring uses six weighted signals plus phase boosts:

  | Signal | Weight |
  |---|---|
  | relevance (avg evidence weight) | 0.30 |
  | frequency (log-scaled evidence count) | 0.24 |
  | query_diversity (distinct threads/paths/entities) | 0.15 |
  | recency (half-life decayed, `dreaming_recency_half_life_days`) | 0.15 |
  | consolidation (distinct run ids) | 0.10 |
  | conceptual_richness (text + entity breadth) | 0.06 |

  Light and REM hits add capped boosts (defaults 0.05 and 0.08). Contradiction
  and volatility penalties still apply.

Promotion requires **all** of:

- `score ≥ dreaming_promotion_min_score` (default 0.8)
- `evidence_count ≥ dreaming_promotion_min_evidence_count` (default 3)
- `source_diversity ≥ dreaming_promotion_min_source_diversity` (default 3)
- revalidation passed
- no active contradiction
- `age_days ≤ dreaming_max_age_days` (default 30)

The `repo-memory-dreaming-daemon --explain <repo>` flag runs the Deep-phase
scoring without mutating claims or writing a snapshot; it prints each claim's
score, would-promote verdict, and failed gates.

## Behavior

The current flow is:

1. Tool middleware updates `dirty_paths`, `focus_paths`, and `focus_entities`.
2. Before-model middleware resolves the runtime, probes git-style changed paths when `dirty_unknown` is set, and flushes bounded dirty files into repo memory before building context.
3. Event memory stores append-only repo events such as design decisions and watchouts.
4. The standalone Dreaming daemon discovers all repos from Postgres, claims a per-repo lease, reads new signals after the current Dreaming cursor, and updates durable claims and snapshots.
5. Dreaming converts events into source-derived claims, revalidates them by `claim_kind`, and compiles repo-core snapshots tagged with a `source_watermark`.
6. Before-model middleware injects the latest snapshot plus a small overlay built only from signals newer than the snapshot watermark. If no snapshot exists yet, it falls back to the legacy block compiler instead of running Dreaming inline inside the agent process.
7. Retrieval tools search current entities and repo history without mutating exact tool outputs. On the Postgres path, entity retrieval uses persisted pgvector embeddings; on the in-memory path, it falls back to lexical ranking.
8. The durable Postgres path requires the explicit migration step. If the schema is missing or out of date, the store and daemon fail fast with a migration error instead of creating tables implicitly.

## End-to-end probe against a real repository

To see indexing + Dreaming run against a real checkout (not just test fixtures):

```bash
# 1. Start local Postgres + pgvector
make postgres-up

# 2. Apply the repo-memory schema
make repo-memory-migrate

# 3. Optional: point at OpenAI embeddings. Otherwise the probe uses deterministic hashed embeddings.
export OPENAI_API_KEY=sk-...

# 4. Index this repo, seed dreaming events, run two Dreaming passes, emit a JSON report
make repo-memory-probe REPO=langchain-ai/open-swe PROBE_ARGS="--reset"

# Or against any other checkout:
make repo-memory-probe PROBE_PATH=/path/to/other/repo REPO=owner/name PROBE_ARGS="--reset --max-files 400"
```

The probe prints a JSON report with:

- `flush` — file count and final `observed_seq` after Tree-sitter sync
- `dreaming_runs` — both passes' cursor/promoted/snapshot ids
- `claims` — every claim's status, score, scope, and any failed promotion gates
- `snapshot` — the compiled `repo_core_snapshot` contents
- `similar_code` — pgvector-backed results for a sample query (override with `PROBE_ARGS="--query 'your question'"`)
- `explain` — per-claim dry-run of the Deep-phase gate outcomes

If the output shows `claims[*].status == "promoted"`, `snapshot != null`, and
`similar_code[*].explanation` containing `vector=…`, you have a working
Tree-sitter → pgvector → Dreaming → snapshot → retrieval path end to end.

## Testing

Repo-memory coverage lives under `tests/repo_memory/`.

Run the focused suite with:

```bash
make test TEST_FILE=tests/repo_memory/
```

Postgres-backed repo-memory tests now try to start the local Compose harness automatically and fail if a real pgvector database cannot be reached.

Key smoke tests:

- `tests/repo_memory/test_end_to_end_repo_memory.py`
- `tests/repo_memory/test_agent_wiring.py`

## Limitations

- The Postgres path depends on an explicit migration step (`uv run repo-memory-migrate` or `make repo-memory-migrate`). Production startup fails fast if the schema or `pgvector` extension is missing instead of silently degrading.
- OpenAI embeddings are the canonical provider for the durable/pgvector path; deterministic hashed embeddings are test-only (they still implement the production `EmbeddingProvider` interface so pgvector queries are exercised end to end).
- Docker-backed validation depends on a running local Docker daemon.
- Git provenance is best-effort and deep history is still lightweight. `get_entity_history` reports `deep_history: []` when unavailable and never fabricates data.
- Full sandbox harness e2e is not verified beyond the focused repo-memory tests.
- The Dreaming layer currently derives claims from repo events, not from a broader trace ingestion pipeline.
- The in-memory adapter is retained only for unit tests and is gated by `REPO_MEMORY_ALLOW_IN_MEMORY=true`.
