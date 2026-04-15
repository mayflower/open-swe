from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class _ExecuteResult:
    output: str = ""
    exit_code: int = 0
    truncated: bool = False


class _FakeBackend:
    def __init__(self, sandbox_id: str, health_result: _ExecuteResult | Exception | None = None) -> None:
        self.id = sandbox_id
        self._health_result = health_result or _ExecuteResult(exit_code=0)

    def execute(self, command: str, *, timeout: int | None = None):
        del timeout
        if command == "echo ok":
            if isinstance(self._health_result, Exception):
                raise self._health_result
            return self._health_result
        return _ExecuteResult(exit_code=0)


class _DummyAgent:
    def with_config(self, config):
        return self


def _execution_config() -> dict:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
        },
        "metadata": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agent_sandbox", "langsmith"])
async def test_check_or_recreate_sandbox_recreates_on_any_health_exception(provider: str) -> None:
    stale_backend = _FakeBackend("stale", health_result=RuntimeError("sandbox down"))
    replacement_backend = _FakeBackend("replacement")

    with (
        patch("agent.server._recreate_sandbox", new_callable=AsyncMock, return_value=replacement_backend) as mock_recreate,
        patch.dict("os.environ", {"SANDBOX_TYPE": provider}),
    ):
        from agent.server import check_or_recreate_sandbox

        backend = await check_or_recreate_sandbox(stale_backend, "thread-123")

    assert backend is replacement_backend
    mock_recreate.assert_called_once_with("thread-123")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agent_sandbox", "langsmith"])
async def test_check_or_recreate_sandbox_recreates_on_nonzero_health_exit(provider: str) -> None:
    stale_backend = _FakeBackend("stale", health_result=_ExecuteResult(exit_code=1, output="not ok"))
    replacement_backend = _FakeBackend("replacement")

    with (
        patch("agent.server._recreate_sandbox", new_callable=AsyncMock, return_value=replacement_backend) as mock_recreate,
        patch.dict("os.environ", {"SANDBOX_TYPE": provider}),
    ):
        from agent.server import check_or_recreate_sandbox

        backend = await check_or_recreate_sandbox(stale_backend, "thread-123")

    assert backend is replacement_backend
    mock_recreate.assert_called_once_with("thread-123")


@pytest.mark.asyncio
async def test_get_agent_recreates_unhealthy_cached_sandbox_before_refreshing_github_access() -> None:
    config = _execution_config()
    stale_backend = _FakeBackend("team-a/stale", health_result=RuntimeError("sandbox down"))
    replacement_backend = _FakeBackend("team-a/replacement")
    mock_client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    with (
        patch("agent.server.resolve_github_token", new_callable=AsyncMock, return_value=("ghp", "enc")),
        patch("agent.server.get_sandbox_id_from_metadata", new_callable=AsyncMock, return_value=stale_backend.id),
        patch(
            "agent.server.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="ghs_fresh",
        ) as mock_install_token,
        patch("agent.server._recreate_sandbox", new_callable=AsyncMock, return_value=replacement_backend) as mock_recreate,
        patch("agent.server.configure_github_network_access") as mock_configure,
        patch("agent.server.aresolve_sandbox_work_dir", new_callable=AsyncMock, return_value="/workspace"),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server.client", mock_client),
        patch.dict("agent.server.SANDBOX_BACKENDS", {"thread-123": stale_backend}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": "agent_sandbox"}),
    ):
        from agent.server import get_agent

        await get_agent(config)

    mock_recreate.assert_called_once_with("thread-123")
    assert mock_install_token.call_count == 1
    mock_configure.assert_called_once_with(replacement_backend, "ghs_fresh")


@pytest.mark.asyncio
async def test_get_agent_persists_namespace_qualified_agent_sandbox_id_in_thread_metadata() -> None:
    config = _execution_config()
    backend = _FakeBackend("team-a/claim-123")
    mock_client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    with (
        patch("agent.server.resolve_github_token", new_callable=AsyncMock, return_value=("ghp", "enc")),
        patch("agent.server.get_sandbox_id_from_metadata", new_callable=AsyncMock, return_value=None),
        patch("agent.server._create_sandbox_with_github_access", new_callable=AsyncMock, return_value=backend),
        patch("agent.server.aresolve_sandbox_work_dir", new_callable=AsyncMock, return_value="/workspace"),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server.client", mock_client),
        patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": "agent_sandbox"}),
    ):
        from agent.server import get_agent

        await get_agent(config)

    mock_client.threads.update.assert_any_call(
        thread_id="thread-123",
        metadata={"sandbox_id": "team-a/claim-123"},
    )
