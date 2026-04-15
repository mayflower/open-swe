from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

try:
    from langgraph.config import get_config
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs
    def get_config() -> dict[str, Any]:
        raise RuntimeError("langgraph is not available")

from .config import RepoMemoryConfig
from .persistence.repositories import create_repo_memory_store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RepoMemoryRuntime:
    repo: str
    store: object = field(default_factory=lambda: create_repo_memory_store(RepoMemoryConfig()))
    config: RepoMemoryConfig = field(default_factory=RepoMemoryConfig)
    sandbox_backend: Any | None = None
    work_dir: str | None = None
    dreaming_daemon_thread: threading.Thread | None = None
    dreaming_daemon_stop: threading.Event = field(default_factory=threading.Event)
    dreaming_daemon_lock: threading.Lock = field(default_factory=threading.Lock)


DEFAULT_RUNTIME = RepoMemoryRuntime(repo="unknown")

_RUNTIME_REGISTRY: dict[str, RepoMemoryRuntime] = {}


def get_or_create_repo_memory_runtime(
    repo: str,
    *,
    config: RepoMemoryConfig | None = None,
) -> RepoMemoryRuntime:
    runtime_config = config or RepoMemoryConfig()
    runtime = _RUNTIME_REGISTRY.get(repo)
    if runtime is None:
        runtime = RepoMemoryRuntime(
            repo=repo,
            store=create_repo_memory_store(runtime_config),
            config=runtime_config,
        )
        _RUNTIME_REGISTRY[repo] = runtime
    else:
        runtime.config = runtime_config
        desired_backend = runtime_config.resolved_backend()
        current_backend = "postgres" if runtime_attr(runtime.store, "database_url") else "memory"
        current_database_url = runtime_attr(runtime.store, "database_url")
        if (
            desired_backend != current_backend
            or current_database_url != runtime_config.database_url
        ):
            runtime.store = create_repo_memory_store(runtime_config)
    return runtime


def get_registered_repo_memory_runtime(repo: str) -> RepoMemoryRuntime | None:
    return _RUNTIME_REGISTRY.get(repo)


def bind_runtime_context(
    runtime: RepoMemoryRuntime,
    *,
    sandbox_backend: Any | None = None,
    work_dir: str | None = None,
) -> RepoMemoryRuntime:
    if sandbox_backend is not None:
        runtime.sandbox_backend = sandbox_backend
    if work_dir:
        runtime.work_dir = work_dir
    return runtime


def ensure_repo_memory_dreaming_daemon(runtime: RepoMemoryRuntime) -> threading.Thread | None:
    from .dreaming import run_repo_memory_dreaming_pass, supports_dreaming

    if not runtime.repo or not supports_dreaming(runtime.store):
        return None

    with runtime.dreaming_daemon_lock:
        current = runtime.dreaming_daemon_thread
        if current is not None and current.is_alive():
            return current

        runtime.dreaming_daemon_stop.clear()

        def _runner() -> None:
            worker_id = f"dreaming-daemon:{runtime.repo}"
            while not runtime.dreaming_daemon_stop.is_set():
                try:
                    run_repo_memory_dreaming_pass(runtime, worker_id=worker_id)
                except Exception:
                    logger.exception("repo_memory_dreaming_daemon_failed repo=%s", runtime.repo)
                if runtime.dreaming_daemon_stop.wait(
                    runtime.config.dreaming_daemon_poll_interval_seconds
                ):
                    break

        thread = threading.Thread(
            target=_runner,
            name=f"repo-memory-dreaming:{runtime.repo}",
            daemon=True,
        )
        runtime.dreaming_daemon_thread = thread
        thread.start()
        logger.info("repo_memory_dreaming_daemon_started repo=%s", runtime.repo)
        return thread


def stop_repo_memory_dreaming_daemon(runtime: RepoMemoryRuntime, *, timeout: float = 1.0) -> None:
    runtime.dreaming_daemon_stop.set()
    thread = runtime.dreaming_daemon_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def runtime_attr(runtime: object, name: str, default: Any = None) -> Any:
    if isinstance(runtime, Mapping):
        return runtime.get(name, default)
    return getattr(runtime, name, default)


def resolve_runtime_from_context(state: Mapping[str, Any] | None = None) -> object | None:
    if state is not None:
        runtime = state.get("repo_memory_runtime")
        if runtime_attr(runtime, "repo") and runtime_attr(runtime, "store"):
            return runtime

    try:
        config = get_config()
    except Exception:
        return None

    metadata = config.get("metadata", {})
    runtime = metadata.get("repo_memory_runtime")
    if runtime_attr(runtime, "repo") and runtime_attr(runtime, "store"):
        return runtime

    repo_full_name = metadata.get("repo_full_name")
    if isinstance(repo_full_name, str) and repo_full_name:
        return get_registered_repo_memory_runtime(repo_full_name)

    repo_config = metadata.get("repo", {})
    if isinstance(repo_config, dict) and repo_config.get("owner") and repo_config.get("name"):
        repo = f"{repo_config['owner']}/{repo_config['name']}"
        return get_registered_repo_memory_runtime(repo)

    return None
