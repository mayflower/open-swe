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


def test_agent_sandbox_bootstrap_writes_git_credentials_without_export_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "agent_sandbox")
    backend = _FakeSandboxBackend()

    from agent.utils.sandbox_github_auth import configure_github_network_access

    configure_github_network_access(backend, "ghs_install")

    assert any("git config --global credential.helper store" in cmd for cmd in backend.commands)
    assert any(".git-credentials" in cmd for cmd in backend.commands)
    assert not any("export " in cmd for cmd in backend.commands)


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
        patch("agent.server.resolve_github_token", new_callable=AsyncMock, return_value=("ghp", "enc")),
        patch("agent.server.get_sandbox_id_from_metadata", new_callable=AsyncMock, return_value=None),
        patch("agent.server._create_sandbox_with_github_access", new_callable=AsyncMock, return_value=mock_sandbox) as mock_create,
        patch("agent.server.aresolve_sandbox_work_dir", new_callable=AsyncMock, return_value="/workspace"),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server.client", mock_client),
        patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": provider}),
    ):
        from agent.server import get_agent

        await get_agent(config)

        mock_create.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["agent_sandbox", "langsmith"])
async def test_get_agent_refreshes_github_access_when_reconnecting_existing_sandbox(
    provider: str,
) -> None:
    config = _execution_config()
    mock_sandbox = _FakeSandboxBackend(sandbox_id=f"{provider}-existing")
    mock_client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    with (
        patch("agent.server.resolve_github_token", new_callable=AsyncMock, return_value=("ghp", "enc")),
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
        patch("agent.server.configure_github_network_access") as mock_configure,
        patch(
            "agent.server.check_or_recreate_sandbox",
            new_callable=AsyncMock,
            return_value=mock_sandbox,
        ),
        patch("agent.server.aresolve_sandbox_work_dir", new_callable=AsyncMock, return_value="/workspace"),
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
        mock_configure.assert_called_once_with(mock_sandbox, "ghs_fresh")


def test_commit_and_open_pr_refreshes_github_access_before_fetch_and_push() -> None:
    sandbox_backend = _FakeSandboxBackend()
    order: list[str] = []

    def _record_refresh(_backend, token: str) -> None:
        order.append(f"refresh:{token}")

    def _record_fetch(*args, **kwargs):
        del args, kwargs
        order.append("fetch")
        return _ExecuteResult()

    def _record_push(*args, **kwargs):
        del args, kwargs
        order.append("push")
        return _ExecuteResult()

    with (
        patch(
            "agent.tools.commit_and_open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-123",
                    "repo": {"owner": "langchain-ai", "name": "open-swe"},
                },
                "metadata": {"branch_name": "open-swe/thread-123"},
            },
        ),
        patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync", return_value=sandbox_backend),
        patch("agent.tools.commit_and_open_pr.resolve_repo_dir", return_value="/workspace/open-swe"),
        patch("agent.tools.commit_and_open_pr.get_github_token", return_value="ghp_user"),
        patch("agent.tools.commit_and_open_pr.resolve_triggering_user_identity", return_value=None),
        patch("agent.tools.commit_and_open_pr.add_pr_collaboration_note", side_effect=lambda body, _: body),
        patch("agent.tools.commit_and_open_pr.add_user_coauthor_trailer", side_effect=lambda message, _: message),
        patch("agent.tools.commit_and_open_pr.git_has_uncommitted_changes", return_value=True),
        patch("agent.tools.commit_and_open_pr.git_fetch_origin", side_effect=_record_fetch),
        patch("agent.tools.commit_and_open_pr.git_has_unpushed_commits", return_value=False),
        patch("agent.tools.commit_and_open_pr.git_current_branch", return_value="open-swe/thread-123"),
        patch("agent.tools.commit_and_open_pr.git_config_user"),
        patch("agent.tools.commit_and_open_pr.git_add_all"),
        patch("agent.tools.commit_and_open_pr.git_commit", return_value=_ExecuteResult()),
        patch(
            "agent.tools.commit_and_open_pr.get_github_app_installation_token",
            side_effect=["ghs_fetch", "ghs_push", "ghs_api"],
        ) as mock_install_token,
        patch("agent.tools.commit_and_open_pr.configure_github_network_access", side_effect=_record_refresh),
        patch("agent.tools.commit_and_open_pr.git_push", side_effect=_record_push),
        patch("agent.tools.commit_and_open_pr.get_github_default_branch", return_value="main"),
        patch(
            "agent.tools.commit_and_open_pr.create_github_pr",
            return_value=("https://github.com/langchain-ai/open-swe/pull/1", 1, False),
        ),
    ):
        from agent.tools.commit_and_open_pr import commit_and_open_pr

        result = commit_and_open_pr("feat: add sandbox auth [closes SWE-1]", "## Description\nx\n\n## Test Plan\n- [ ] x")

    assert result["success"] is True
    assert mock_install_token.call_count == 3
    refresh_positions = [idx for idx, event in enumerate(order) if event.startswith("refresh:")]
    assert len(refresh_positions) == 2
    assert refresh_positions[0] < order.index("fetch")
    assert order.index("fetch") < refresh_positions[1] < order.index("push")
