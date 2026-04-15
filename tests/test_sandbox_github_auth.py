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


class _FakeSandboxBackend:
    def __init__(self, *, sandbox_id: str = "sandbox-123") -> None:
        self.id = sandbox_id
        self.commands: list[str] = []
        self._responses: list[_ExecuteResult] = []

    def queue_response(self, response: _ExecuteResult) -> None:
        self._responses.append(response)

    def execute(self, command: str, *, timeout: int | None = None) -> _ExecuteResult:
        del timeout
        self.commands.append(command)
        if self._responses:
            return self._responses.pop(0)
        return _ExecuteResult()


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


def test_shared_helper_dispatches_to_langsmith_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    backend = _FakeSandboxBackend(sandbox_id="sandbox-langsmith")

    from agent.utils.sandbox_github_auth import configure_github_network_access

    with patch("agent.utils.sandbox_github_auth._configure_github_proxy") as mock_proxy:
        configure_github_network_access(backend, "ghs_install")

    mock_proxy.assert_called_once_with("sandbox-langsmith", "ghs_install")


def test_agent_sandbox_bootstrap_loads_token_into_in_memory_credential_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Token must never be written to a plain-text file on disk.

    The new flow loads the GitHub App installation token into ``git
    credential-cache--daemon`` (in-process memory only, served via a unix
    socket on demand). The wrapper for ``gh`` reads from the same cache so
    ``gh`` never persists the token to ``~/.config/gh/hosts.yml`` either.
    """
    monkeypatch.setenv("SANDBOX_TYPE", "agent_sandbox")
    backend = _FakeSandboxBackend()

    from agent.utils.sandbox_github_auth import configure_github_network_access

    configure_github_network_access(backend, "ghs_install")

    commands = backend.commands
    # Configures the in-memory credential cache, not the on-disk store.
    assert any("credential.helper" in cmd and "cache --timeout=" in cmd for cmd in commands)
    assert not any("credential.helper store" in cmd for cmd in commands)
    # Token gets fed via stdin into `git credential approve`; nothing writes
    # to ``~/.git-credentials``.
    assert any("git credential approve" in cmd for cmd in commands)
    assert not any('> "$HOME/.git-credentials"' in cmd for cmd in commands)
    # Any pre-existing on-disk credentials are explicitly cleared so an
    # earlier run on the same sandbox can't leave the token behind.
    assert any("rm -f " in cmd and ".git-credentials" in cmd for cmd in commands)
    # gh-secure wrapper installed so ``gh`` can fetch credentials from the
    # cache without writing to ``~/.config/gh/hosts.yml``.
    assert any("/usr/local/bin/gh-secure" in cmd for cmd in commands)
    assert not any("export " in cmd for cmd in commands)


def test_agent_sandbox_bootstrap_raises_runtime_error_with_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "agent_sandbox")
    backend = _FakeSandboxBackend()
    backend.queue_response(_ExecuteResult())
    backend.queue_response(_ExecuteResult(output="permission denied", exit_code=1))

    from agent.utils.sandbox_github_auth import configure_github_network_access

    with pytest.raises(RuntimeError, match="permission denied"):
        configure_github_network_access(backend, "ghs_install")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agent_sandbox", "langsmith"])
async def test_get_agent_uses_create_helper_for_new_sandboxes(provider: str) -> None:
    config = _execution_config()
    mock_sandbox = _FakeSandboxBackend(sandbox_id=f"{provider}-new")
    mock_client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    with (
        patch(
            "agent.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch(
            "agent.server.get_sandbox_id_from_metadata", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "agent.server._create_sandbox_with_proxy",
            new_callable=AsyncMock,
            return_value=mock_sandbox,
        ) as mock_create,
        patch(
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server.client", mock_client),
        patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": provider}),
    ):
        from agent.server import get_agent

        await get_agent(config)

        mock_create.assert_called_once_with(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agent_sandbox", "langsmith"])
async def test_get_agent_refreshes_github_access_when_reconnecting_existing_sandbox(
    provider: str,
) -> None:
    config = _execution_config()
    mock_sandbox = _FakeSandboxBackend(sandbox_id=f"{provider}-existing")
    mock_client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    with (
        patch(
            "agent.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch(
            "agent.server.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=mock_sandbox.id,
        ),
        patch("agent.server.create_sandbox", return_value=mock_sandbox) as mock_create,
        patch(
            "agent.server.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value="ghs_fresh",
        ) as mock_install_token,
        # Patch both helpers so the same test exercises both providers; the
        # provider-specific assertion below selects whichever the code path
        # actually invoked.
        patch("agent.server._configure_github_proxy") as mock_proxy,
        patch("agent.server.configure_github_network_access") as mock_configure,
        patch(
            "agent.server.check_or_recreate_sandbox",
            new_callable=AsyncMock,
            return_value=mock_sandbox,
        ),
        patch(
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server.client", mock_client),
        patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": provider}),
    ):
        from agent.server import get_agent

        await get_agent(config)

        mock_create.assert_called_once_with(mock_sandbox.id)
        mock_install_token.assert_called_once_with()
        if provider == "langsmith":
            mock_proxy.assert_called_once_with(mock_sandbox.id, "ghs_fresh")
            mock_configure.assert_not_called()
        else:
            mock_configure.assert_called_once_with(mock_sandbox, "ghs_fresh")
            mock_proxy.assert_not_called()


# test_commit_and_open_pr_refreshes_github_access_before_fetch_and_push was
# removed: upstream removed the agent/tools/commit_and_open_pr.py tool; the
# agent now drives commit/push/PR via ``GH_TOKEN=dummy gh`` directly.
