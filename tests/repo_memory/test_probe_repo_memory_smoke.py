"""Smoke tests for scripts/probe_repo_memory.

These exercise the probe's helper functions against the in-memory store so
that we cover the indexing + seeding + dreaming + search flow without
depending on a running Postgres harness. A separate Postgres-backed test is
run via the probe itself (`make repo-memory-probe`) when a real pgvector
database is available.
"""

from __future__ import annotations

from pathlib import Path

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.persistence.repositories import InMemoryRepoMemoryStore
from agent.repo_memory.runtime import RepoMemoryRuntime
from scripts import probe_repo_memory


def _permissive_config() -> RepoMemoryConfig:
    return RepoMemoryConfig(
        embedding_provider="hashed",
        embedding_dimensions=16,
        dreaming_promotion_min_score=0.0,
        dreaming_promotion_min_evidence_count=1,
        dreaming_promotion_min_source_diversity=1,
    )


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "probe-repo"
    root.mkdir()
    (root / "agent").mkdir()
    (root / "agent" / "alpha.py").write_text(
        '"""Alpha module."""\n\nclass Alpha:\n    def send(self, payload):\n        return payload\n'
    )
    (root / "agent" / "beta.ts").write_text(
        "export interface Beta { label: string; }\n"
        "export class BetaClient { send(x: Beta) { return x.label; } }\n"
    )
    (root / "agent" / "gamma.go").write_text(
        "package gamma\n\ntype GammaClient struct{}\n\n"
        "func (c *GammaClient) Send(value string) string { return value }\n"
    )
    (root / "agent" / "delta.rs").write_text(
        "pub trait DeltaSender { fn send(&self, value: &str); }\n\n"
        "pub struct DeltaClient;\n\n"
        "impl DeltaClient { pub fn send(&self, value: &str) {} }\n"
    )
    # Ignored paths — probe should skip these.
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "noise.ts").write_text("export function noise() {}\n")
    return root


def test_probe_indexes_multilang_repo_and_seeds_dreaming(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    store = InMemoryRepoMemoryStore()
    runtime = RepoMemoryRuntime(repo="probe/smoke", store=store, config=_permissive_config())

    flush_summary = probe_repo_memory._flush_source_tree(
        store, runtime.repo, repo, max_files=100, chunk_size=2
    )
    assert flush_summary["files"] == 4  # 4 real files; node_modules/.git skipped
    entity_ids = {entity.entity_id for entity in store.iter_entities(runtime.repo)}
    assert f"{runtime.repo} unused" not in entity_ids  # sanity
    assert any("alpha.py" in entity_id for entity_id in entity_ids)
    assert any("beta.ts" in entity_id for entity_id in entity_ids)
    assert any("gamma.go" in entity_id for entity_id in entity_ids)
    assert any("delta.rs" in entity_id for entity_id in entity_ids)

    seeded = probe_repo_memory._seed_events(
        store, runtime.repo, flush_summary["last_observed_seq"]
    )
    assert seeded == len(probe_repo_memory._SEED_EVENTS)

    runs = probe_repo_memory._run_dreaming(runtime)
    assert len(runs) == 2
    assert all(run["status"] == "succeeded" for run in runs)
    assert any(run["promoted_count"] >= 1 for run in runs)

    snapshot = probe_repo_memory._summarize_snapshot(store, runtime.repo)
    assert snapshot is not None
    assert snapshot["source_claim_keys"]

    claims = probe_repo_memory._summarize_claims(store, runtime.repo)
    assert claims, "expected at least one claim after dreaming"
    assert any(claim["status"] == "promoted" for claim in claims)

    search = probe_repo_memory._summarize_search(
        store, runtime.repo, "send payload helper", runtime.config
    )
    assert search, "vector search should return something for a multi-language repo"

    explain = probe_repo_memory._summarize_explain(runtime)
    assert explain, "explain should report per-claim gate outcomes"
