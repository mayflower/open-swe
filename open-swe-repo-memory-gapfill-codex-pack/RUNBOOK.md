# Runbook

## Recommended setup

- Start Codex from the repository root.
- Work in a single thread or worktree for this pack.
- Avoid running two Codex threads that modify the same repo-memory files at the same time.
- Keep the discovery document up to date if the implementation shape changes.
- Prefer the local Docker Compose Postgres/pgvector stack for integration validation when the repo already provides it.

## Execution order

1. `PROMPTS/00_start_here_bootstrap_and_baseline.md`
2. `01_red_runtime_handoff_into_state.md`
3. `02_green_runtime_handoff_into_state.md`
4. `03_refactor_runtime_handoff_into_state.md`
5. `04_red_before_model_flush_and_sync.md`
6. `05_green_before_model_flush_and_sync.md`
7. `06_refactor_before_model_flush_and_sync.md`
8. `07_red_execute_delta_probe_and_config.md`
9. `08_green_execute_delta_probe_and_config.md`
10. `09_refactor_execute_delta_probe_and_config.md`
11. `10_red_multilanguage_sync_routing.md`
12. `11_green_multilanguage_sync_routing.md`
13. `12_refactor_multilanguage_sync_routing.md`
14. `13_red_end_to_end_auto_refresh_flow.md`
15. `14_green_end_to_end_auto_refresh_flow.md`
16. `15_refactor_end_to_end_and_readme_truthfulness.md`
17. `16_final_validation_and_release_notes.md`

## Prompt execution rules

- Do not skip phases.
- If RED tests unexpectedly pass, stop and investigate before moving on.
- If GREEN requires large unrelated refactors, stop and split a smaller seam first.
- If REFACTOR breaks tests, revert to the last green state and try a smaller refactor.
- If a prompt reaches the point where embeddings provider or schema shape becomes ambiguous, stop and record the decision explicitly in discovery docs before continuing.

## Suggested commit discipline

If you are using local git commits while iterating:
- one commit per green slice is ideal
- avoid committing during RED unless the repo requires it for checkpointing
- use the final validation prompt to decide whether docs and code are consistent enough for a PR

## Discovery artifact

The bootstrap prompt creates:

- `docs/repo_memory_gapfill_discovery.md`
- `docs/repo_memory_gapfill_baseline.md`

Every later prompt assumes those files exist and uses them as the source of truth for:
- actual file paths
- actual test commands
- actual symbol names
- Docker Compose / Postgres setup details
- env var or connection-string conventions
- any divergence from the assumptions in this pack
