"""Concurrency / parallel-realtime tests for repo_memory.

These exercise the gaps that would silently corrupt or lose data when many
agents work the same repo at once: racy ``observed_seq`` allocation,
event-id collisions, claim dedup, lineage updates, runtime registry, and
the LISTEN/NOTIFY freshness signal.

Each test that hits Postgres uses the ``postgres_store`` fixture from
``conftest.py``; the fixture truncates between tests so they don't bleed.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from agent.repo_memory.config import RepoMemoryConfig
from agent.repo_memory.domain import (
    ClaimKind,
    ClaimScopeKind,
    ClaimStatus,
    MemoryClaim,
    RepoEventKind,
    RevalidationMode,
    make_repo_event_id,
)
from agent.repo_memory.events import remember_decision_event
from agent.repo_memory.persistence import notifier as notifier_mod
from agent.repo_memory.persistence import pool as pool_mod
from agent.repo_memory.persistence.postgres import (
    PostgresRepoMemoryStore,
    _advisory_lock_keys,
)
from agent.repo_memory.runtime import (
    _RUNTIME_REGISTRY,
    RepoMemoryRuntime,
    get_or_create_repo_memory_runtime,
)

# ----- pure-python (no DB) ---------------------------------------------------


def test_make_repo_event_id_is_unique_under_collisions() -> None:
    ids = {make_repo_event_id("acme/foo", 7, RepoEventKind.DECISION) for _ in range(200)}
    assert len(ids) == 200


def test_advisory_lock_keys_are_deterministic_and_collide_per_source() -> None:
    a = _advisory_lock_keys("acme/foo", "src-1")
    b = _advisory_lock_keys("acme/foo", "src-1")
    c = _advisory_lock_keys("acme/foo", "src-2")
    assert a == b
    assert a != c


def test_runtime_registry_is_single_instance_under_parallel_creates() -> None:
    _RUNTIME_REGISTRY.clear()
    config = RepoMemoryConfig(backend="memory")
    runtimes: list[RepoMemoryRuntime] = []
    lock = threading.Lock()

    def _create() -> None:
        runtime = get_or_create_repo_memory_runtime("acme/concurrent", config=config)
        with lock:
            runtimes.append(runtime)

    threads = [threading.Thread(target=_create) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({id(runtime) for runtime in runtimes}) == 1


# ----- pool ------------------------------------------------------------------


def test_pool_is_cached_per_database_url(postgres_url: str) -> None:
    first = pool_mod.get_pool(postgres_url)
    second = pool_mod.get_pool(postgres_url)
    assert first is second


# ----- server-side seq allocation -------------------------------------------


def test_allocate_observed_seq_postgres_is_collision_free(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    repo = "acme/seq"
    seqs: list[int] = []
    seq_lock = threading.Lock()

    def _allocate() -> None:
        value = postgres_store.allocate_observed_seq(repo)
        with seq_lock:
            seqs.append(value)

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(lambda _: _allocate(), range(50)))

    assert sorted(seqs) == list(range(1, 51))
    assert len(set(seqs)) == 50
    assert postgres_store.get_sync_state(repo)["last_observed_seq"] == 50


# ----- transactional Light dedup --------------------------------------------


def _claim(repo: str, source_identity: str, text: str) -> MemoryClaim:
    now = datetime.now(UTC)
    embedding = [0.1] * 16
    return MemoryClaim(
        claim_id=f"{repo}:{source_identity}",
        claim_key=f"{repo}:{source_identity}",
        source_identity_key=source_identity,
        repo=repo,
        scope_kind=ClaimScopeKind.REPO,
        scope_ref=repo,
        claim_kind=ClaimKind.DESIGN_DECISION,
        text=text,
        normalized_text=text,
        status=ClaimStatus.CANDIDATE,
        first_seen_at=now,
        last_seen_at=now,
        last_revalidated_at=None,
        revalidation_mode=RevalidationMode.EVIDENCE_ONLY,
        embedding=embedding,
        embedding_provider="hashed",
        embedding_dimensions=16,
        embedding_version="sha256-token-v1",
    )


def test_upsert_candidate_with_dedup_serializes_concurrent_writers(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    repo = "acme/dedup"
    source_identity = "shared-source"

    def _writer(idx: int) -> None:
        candidate = _claim(repo, source_identity, f"decision text v{idx}")
        postgres_store.upsert_candidate_with_dedup(
            candidate,
            merge_similarity_threshold=0.82,
            jaccard_threshold=0.9,
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(_writer, range(20)))

    claims = postgres_store.list_claims(repo)
    assert len(claims) == 1, [c.claim_key for c in claims]
    # The merge path may or may not bump merged_source_identities depending on
    # which writer landed first, but the row must be a single canonical claim
    # keyed by source_identity_key.
    assert claims[0].source_identity_key == source_identity


# ----- lineage convergence --------------------------------------------------


def test_record_lineage_converges_under_parallel_writers(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    from agent.repo_memory.domain import EntityKind, EntityRevision

    repo = "acme/lineage"
    base_revision = EntityRevision(
        entity_id="entity-1",
        repo=repo,
        path="src/main.py",
        language="python",
        kind=EntityKind.FUNCTION,
        name="main",
        qualified_name="main",
        observed_seq=1,
        retrieval_text="def main(): ...",
    )
    postgres_store.upsert_entity_revision(base_revision)

    def _link(idx: int) -> None:
        postgres_store.record_lineage(
            "entity-1", f"predecessor-{idx}", reason="rename", confidence=0.9
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(_link, range(15)))

    entity = postgres_store.get_entity("entity-1")
    assert entity is not None
    assert sorted(entity.predecessor_ids) == sorted(f"predecessor-{idx}" for idx in range(15))


# ----- LISTEN/NOTIFY --------------------------------------------------------


def test_listen_notify_bumps_version_on_event_insert(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    notifier_mod.reset_for_tests()
    notifier_mod.ensure_listener_started(postgres_url)
    repo = "acme/notify"
    initial = notifier_mod.get_versions(repo)["repo_memory_event"]

    event = remember_decision_event(repo, observed_seq=1, summary="introduce widget")
    postgres_store.append_repo_event(event)

    deadline = datetime.now(UTC).timestamp() + 5.0
    import time as _time

    while datetime.now(UTC).timestamp() < deadline:
        if notifier_mod.get_versions(repo)["repo_memory_event"] > initial:
            break
        _time.sleep(0.05)

    assert notifier_mod.get_versions(repo)["repo_memory_event"] > initial


def test_listen_notify_is_stale_signals_versions(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    import time as _time

    notifier_mod.reset_for_tests()
    notifier_mod.ensure_listener_started(postgres_url)
    repo = "acme/notify-versions"

    token_before = notifier_mod.freshness_token(repo)
    assert notifier_mod.is_stale(repo, dict(token_before)) is False

    event = remember_decision_event(repo, observed_seq=1, summary="watch the bus")
    postgres_store.append_repo_event(event)

    deadline = _time.time() + 5.0
    while _time.time() < deadline and not notifier_mod.is_stale(repo, dict(token_before)):
        _time.sleep(0.05)

    assert notifier_mod.is_stale(repo, dict(token_before)) is True


def test_listen_notify_supervisor_reconnects_after_backend_terminate(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    """Kill the listener's backend connection from postgres' side and verify
    the supervisor reconnects + continues delivering notifications."""
    import asyncio as _asyncio
    import time as _time

    import asyncpg as _asyncpg

    notifier_mod.reset_for_tests()
    notifier_mod.ensure_listener_started(postgres_url)
    repo = "acme/notify-reconnect"

    initial_conn = notifier_mod._LISTENER_CONNECTIONS.get(postgres_url)
    assert initial_conn is not None

    async def _terminate() -> None:
        admin = await _asyncpg.connect(postgres_url)
        try:
            backend_pid = initial_conn.get_server_pid()
            await admin.execute("SELECT pg_terminate_backend($1)", backend_pid)
        finally:
            await admin.close()

    _asyncio.run(_terminate())

    # Wait for supervisor to detect the failure and reconnect.
    deadline = _time.time() + 30.0
    while _time.time() < deadline:
        current = notifier_mod._LISTENER_CONNECTIONS.get(postgres_url)
        if current is not None and current is not initial_conn:
            break
        _time.sleep(0.5)
    new_conn = notifier_mod._LISTENER_CONNECTIONS.get(postgres_url)
    assert new_conn is not None and new_conn is not initial_conn, (
        "supervisor did not reconnect after backend was terminated"
    )

    initial_versions = notifier_mod.get_versions(repo)["repo_memory_event"]
    event = remember_decision_event(repo, observed_seq=1, summary="post-reconnect signal")
    postgres_store.append_repo_event(event)

    deadline = _time.time() + 10.0
    while (
        _time.time() < deadline
        and notifier_mod.get_versions(repo)["repo_memory_event"] == initial_versions
    ):
        _time.sleep(0.05)
    assert notifier_mod.get_versions(repo)["repo_memory_event"] > initial_versions


# ----- async hot-path siblings ----------------------------------------------


async def test_async_siblings_match_sync_results(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    repo = "acme/async-sibling"
    event = remember_decision_event(repo, observed_seq=1, summary="async-test")
    await postgres_store.aappend_repo_event(event)

    sync_events = postgres_store.list_repo_events(repo)
    async_events = await postgres_store.alist_repo_events(repo)
    assert [e.event_id for e in sync_events] == [e.event_id for e in async_events]

    sync_state = postgres_store.get_sync_state(repo)
    async_state = await postgres_store.aget_sync_state(repo)
    assert sync_state == async_state


async def test_aupsert_candidate_with_dedup_serializes(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    import asyncio as _asyncio

    repo = "acme/async-dedup"
    source_identity = "shared-async"

    async def _writer(idx: int) -> None:
        candidate = _claim(repo, source_identity, f"async decision v{idx}")
        await postgres_store.aupsert_candidate_with_dedup(
            candidate,
            merge_similarity_threshold=0.82,
            jaccard_threshold=0.9,
        )

    await _asyncio.gather(*[_writer(idx) for idx in range(20)])
    claims = postgres_store.list_claims(repo)
    assert len(claims) == 1
    assert claims[0].source_identity_key == source_identity


# ----- async agent tools ----------------------------------------------------


async def test_aremember_repo_decision_uses_async_path(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    from unittest.mock import patch as _patch

    from agent.tools.remember_repo_decision import aremember_repo_decision

    repo = "acme/async-tool"
    runtime = RepoMemoryRuntime(repo=repo, store=postgres_store)

    with _patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        outcome = await aremember_repo_decision("use kafka for the queue", path="services/queue.py")

    assert outcome["status"] == "ok"
    assert outcome["repo"] == repo
    events = postgres_store.list_repo_events(repo)
    assert len(events) == 1
    assert events[0].summary == "use kafka for the queue"


async def test_asearch_repo_memory_returns_pgvector_results(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    from unittest.mock import patch as _patch

    from agent.tools.search_repo_memory import asearch_repo_memory

    repo = "acme/async-search"
    runtime = RepoMemoryRuntime(repo=repo, store=postgres_store)
    candidate = _claim(repo, "src-id", "use postgres listen-notify for invalidation")
    postgres_store.upsert_claim(candidate)

    with _patch(
        "agent.repo_memory.runtime.get_config",
        return_value={"metadata": {"repo_memory_runtime": runtime}},
    ):
        result = await asearch_repo_memory("postgres listen notify")

    assert result["repo"] == repo
    assert result["retrieval"] == "pgvector"


async def test_async_injection_path_returns_payload(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    from agent.repo_memory.middleware.injection import abuild_injection_payload

    repo = "acme/async-injection"
    notifier_mod.reset_for_tests()

    runtime = RepoMemoryRuntime(repo=repo, store=postgres_store)
    state: dict = {"repo_memory_runtime": runtime}

    payload = await abuild_injection_payload(state)
    assert payload is not None
    assert "messages" in payload


def test_schema_ready_cache_is_process_wide(postgres_url: str) -> None:
    from agent.repo_memory.embeddings import HashEmbeddingProvider
    from agent.repo_memory.persistence import postgres as postgres_mod

    postgres_mod._SCHEMA_READY_BY_URL.discard(postgres_url)
    provider = HashEmbeddingProvider(dimensions=16)
    first = PostgresRepoMemoryStore(database_url=postgres_url, embedding_provider=provider)
    second = PostgresRepoMemoryStore(database_url=postgres_url, embedding_provider=provider)

    first.list_repositories()
    assert postgres_url in postgres_mod._SCHEMA_READY_BY_URL
    # Second instance must not re-validate; clear its per-instance flag and
    # observe that listing still works (which means _ensure_schema short-circuited).
    second._schema_ready = False
    second.list_repositories()
    assert second._schema_ready is True


# ----- daemon lease isolation -----------------------------------------------


def test_dreaming_lease_serializes_two_concurrent_cycles(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """Two daemons against the same DB on the same repo: exactly one writes,
    the other observes the lease and skips."""
    from agent.repo_memory.config import RepoMemoryConfig as _Config
    from agent.repo_memory.daemon import run_repo_memory_dreaming_cycle

    repo = "acme/lease-collision"
    # Seed an event so dreaming has work to do.
    event = remember_decision_event(repo, observed_seq=1, summary="lease-collision-seed")
    postgres_store.append_repo_event(event)

    config = _Config(backend="postgres", embedding_provider="hashed", embedding_dimensions=16)
    config.database_url = postgres_store.database_url

    results: list[list] = []
    barrier = threading.Barrier(2)

    def _cycle(prefix: str) -> None:
        barrier.wait()
        runs = run_repo_memory_dreaming_cycle(postgres_store, config=config, worker_prefix=prefix)
        results.append(runs)

    t1 = threading.Thread(target=_cycle, args=("daemon-a",))
    t2 = threading.Thread(target=_cycle, args=("daemon-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    all_runs = [run for runs in results for run in runs if run is not None]
    # The contract: at most one cycle does real work on this repo. If both
    # threads got lease at different times the second sees the cursor
    # already advanced and returns succeeded with signal_count==0 — that's
    # still safe. What we forbid is both cycles processing the same events.
    workers_with_signals = [run for run in all_runs if run.signal_count > 0]
    assert len(workers_with_signals) <= 1, (
        "two concurrent dreaming passes both processed events: "
        f"{[(run.status, run.signal_count) for run in all_runs]}"
    )


# ----- embedding batching invariant -----------------------------------------


def test_flush_coordinator_calls_embed_many_once_per_flush() -> None:
    from agent.repo_memory.sync import FlushCoordinator

    class _CountingStore:
        """Plain (non-slotted) store that counts embed_many calls.

        We can't monkey-patch ``InMemoryRepoMemoryStore.upsert_entity_revisions``
        because the dataclass uses ``slots=True``. A plain wrapper class is
        the simplest way to assert the batch-call contract.
        """

        def __init__(self) -> None:
            self.embed_many_calls = 0
            self.embed_many_total_inputs = 0
            self.upserts: list = []
            self.observed_seqs: dict[str, int] = {}

        def allocate_observed_seq(self, repo: str) -> int:
            self.observed_seqs[repo] = self.observed_seqs.get(repo, 0) + 1
            return self.observed_seqs[repo]

        def get_sync_state(self, repo: str) -> dict:
            return {
                "last_observed_seq": self.observed_seqs.get(repo, 0),
                "last_compiled_seq": 0,
                "dreaming_cursor": 0,
            }

        def upsert_file_revision(self, _revision) -> None:
            pass

        def iter_entities_for_path(self, _repo: str, _path: str) -> list:
            return []

        def upsert_entity_revisions(self, revisions: list) -> None:
            # Single call with all revisions == one batched embedding pass.
            self.embed_many_calls += 1
            self.embed_many_total_inputs += len(revisions)
            self.upserts.extend(revisions)

        def set_last_compiled_seq(self, _repo: str, _seq: int) -> None:
            pass

    store = _CountingStore()
    coord = FlushCoordinator(repo="acme/embed-batch", store=store)
    changed_files = {
        "src/a.py": "def alpha():\n    pass\n",
        "src/b.py": "def beta():\n    pass\n",
        "src/c.py": "def gamma():\n    pass\n",
    }
    coord.flush(changed_files=changed_files, observed_seq=1)

    # 3 files × (1 module + 1 function) = 6 revisions, all in one batch.
    assert store.embed_many_calls == 1
    assert store.embed_many_total_inputs == 6


# ----- lifespan / pool shutdown --------------------------------------------


def test_lifespan_invokes_shutdown_hooks() -> None:
    """FastAPI lifespan must call notifier.shutdown + pool.close_all_pools."""
    from unittest.mock import patch as _patch

    from fastapi.testclient import TestClient

    from agent import webapp as webapp_mod
    from agent.utils import sandbox as sandbox_utils

    with (
        _patch.object(webapp_mod.repo_memory_notifier, "shutdown") as notif_shutdown,
        _patch.object(webapp_mod.repo_memory_pool, "close_all_pools") as pool_close,
        _patch.object(sandbox_utils, "validate_sandbox_startup_config"),
    ):
        with TestClient(webapp_mod.app):
            pass
    notif_shutdown.assert_called_once()
    pool_close.assert_called_once()


def test_close_all_pools_actually_closes_pools(postgres_url: str) -> None:
    """Sanity-check teardown: get a pool, close all, verify the pool is closed."""
    from agent.repo_memory.persistence import pool as pool_mod

    pool_mod.reset_pool_for_tests(postgres_url)
    pool = pool_mod.get_pool(postgres_url)
    assert not pool._closed  # type: ignore[attr-defined]
    pool_mod.close_all_pools()
    assert pool._closed  # type: ignore[attr-defined]


# ----- listener-readiness gates the cache -----------------------------------


def test_injection_bypasses_cache_when_listener_not_ready(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """When the listener isn't ready, every call must rebuild from store —
    serving cached state without an active invalidation channel would let
    stale data persist forever."""
    from unittest.mock import patch as _patch

    from agent.repo_memory.middleware.injection import _can_use_cache

    notifier_mod.reset_for_tests()
    # Listener never started -> not ready -> cache must be bypassed.
    assert _can_use_cache(postgres_store.database_url) is False

    # Once the listener is ready, the cache becomes usable.
    notifier_mod.ensure_listener_started(postgres_store.database_url)
    assert _can_use_cache(postgres_store.database_url) is True
    # Patch is_listener_ready where _can_use_cache resolves the symbol — the
    # injection middleware imported it at module load time.
    from agent.repo_memory.middleware import injection as injection_mod

    with _patch.object(injection_mod, "is_listener_ready", return_value=False):
        assert _can_use_cache(postgres_store.database_url) is False


# ----- cross-repo isolation -------------------------------------------------


def test_cross_repo_entity_id_does_not_collide(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """Two repositories with identical path:qualified_name must produce
    distinct rows. Without the repo-scoped entity_id one repo would silently
    overwrite the other's revisions / lineage / current_observed_seq.
    """
    from agent.repo_memory.parsing.python_parser import parse_python_revisions

    source = "def main():\n    return 1\n"
    revisions_alpha = parse_python_revisions("alpha/repo", "src/main.py", source, 1)
    revisions_beta = parse_python_revisions("beta/repo", "src/main.py", source, 1)

    alpha_main = next(rev for rev in revisions_alpha if rev.qualified_name == "main")
    beta_main = next(rev for rev in revisions_beta if rev.qualified_name == "main")
    assert alpha_main.entity_id != beta_main.entity_id
    assert alpha_main.entity_id.startswith("alpha/repo|")
    assert beta_main.entity_id.startswith("beta/repo|")

    postgres_store.upsert_entity_revisions(revisions_alpha)
    postgres_store.upsert_entity_revisions(revisions_beta)

    alpha_entity = postgres_store.get_entity(alpha_main.entity_id)
    beta_entity = postgres_store.get_entity(beta_main.entity_id)

    assert alpha_entity is not None and beta_entity is not None
    assert alpha_entity.repo == "alpha/repo"
    assert beta_entity.repo == "beta/repo"
    assert alpha_entity.entity_id != beta_entity.entity_id


def test_cross_repo_claims_are_isolated(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """A claim with the same source identity in two repos must be two rows."""
    alpha = _claim("alpha/repo", "shared", "use the queue")
    beta = _claim("beta/repo", "shared", "use the queue")
    postgres_store.upsert_claim(alpha)
    postgres_store.upsert_claim(beta)

    alpha_claims = postgres_store.list_claims("alpha/repo")
    beta_claims = postgres_store.list_claims("beta/repo")
    assert len(alpha_claims) == 1
    assert len(beta_claims) == 1
    # Different repos → different claim_keys (claim_key contains repo).
    assert alpha_claims[0].claim_key != beta_claims[0].claim_key
    assert alpha_claims[0].repo == "alpha/repo"
    assert beta_claims[0].repo == "beta/repo"


def test_cross_repo_event_streams_do_not_bleed(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """Events posted to one repo must not surface in the other's stream, and
    each repo's ``observed_seq`` allocator advances independently."""
    alpha_seq = postgres_store.allocate_observed_seq("alpha/repo")
    beta_seq = postgres_store.allocate_observed_seq("beta/repo")
    assert alpha_seq == 1 and beta_seq == 1

    alpha_event = remember_decision_event(
        "alpha/repo", observed_seq=alpha_seq, summary="alpha decision"
    )
    beta_event = remember_decision_event(
        "beta/repo", observed_seq=beta_seq, summary="beta decision"
    )
    postgres_store.append_repo_event(alpha_event)
    postgres_store.append_repo_event(beta_event)

    alpha_events = postgres_store.list_repo_events("alpha/repo")
    beta_events = postgres_store.list_repo_events("beta/repo")
    assert [e.summary for e in alpha_events] == ["alpha decision"]
    assert [e.summary for e in beta_events] == ["beta decision"]

    # Allocator state stays per-repo even after both repos have allocated.
    assert postgres_store.allocate_observed_seq("alpha/repo") == 2
    assert postgres_store.allocate_observed_seq("beta/repo") == 2


def test_cross_repo_listen_notify_versions_are_per_repo(
    postgres_store: PostgresRepoMemoryStore,
    postgres_url: str,
) -> None:
    """A NOTIFY for one repo must not bump the version vector of another."""
    import time as _time

    notifier_mod.reset_for_tests()
    notifier_mod.ensure_listener_started(postgres_url)

    alpha_initial = notifier_mod.get_versions("alpha/repo")["repo_memory_event"]
    beta_initial = notifier_mod.get_versions("beta/repo")["repo_memory_event"]

    event = remember_decision_event("alpha/repo", observed_seq=1, summary="alpha-only signal")
    postgres_store.append_repo_event(event)

    deadline = _time.time() + 5.0
    while (
        _time.time() < deadline
        and notifier_mod.get_versions("alpha/repo")["repo_memory_event"] == alpha_initial
    ):
        _time.sleep(0.05)

    assert notifier_mod.get_versions("alpha/repo")["repo_memory_event"] > alpha_initial
    # beta's vector must be untouched.
    assert notifier_mod.get_versions("beta/repo")["repo_memory_event"] == beta_initial


def test_cross_repo_dreaming_leases_do_not_conflict(
    postgres_store: PostgresRepoMemoryStore,
) -> None:
    """Two repositories should each be able to acquire their own dreaming
    lease at the same time."""
    now = datetime.now(UTC)
    assert postgres_store.acquire_dreaming_lease("alpha/repo", "worker-a", now, ttl_seconds=60)
    assert postgres_store.acquire_dreaming_lease("beta/repo", "worker-b", now, ttl_seconds=60)
    # Within a repo the lease serializes — second worker on alpha is denied.
    assert not postgres_store.acquire_dreaming_lease(
        "alpha/repo", "worker-other", now, ttl_seconds=60
    )
