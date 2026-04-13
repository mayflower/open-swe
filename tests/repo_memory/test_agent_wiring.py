from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.repo_memory.runtime import RepoMemoryRuntime


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
        "metadata": {
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
        },
    }


def test_get_agent_registers_repo_memory_wiring() -> None:
    server = pytest.importorskip("agent.server")
    config = _execution_config()
    mock_sandbox = MagicMock(id="sandbox-cached")
    dummy_agent = _DummyAgent()

    with (
        patch.object(server, "resolve_github_token", new=AsyncMock(return_value=("ghp", "enc"))),
        patch.object(
            server,
            "get_sandbox_id_from_metadata",
            new=AsyncMock(return_value="sandbox-cached"),
        ),
        patch.object(
            server,
            "get_github_app_installation_token",
            new=AsyncMock(return_value="ghs_fresh"),
        ),
        patch.object(server, "_configure_github_proxy"),
        patch.object(server, "aresolve_sandbox_work_dir", new=AsyncMock(return_value="/workspace")),
        patch.object(server, "check_or_recreate_sandbox", new=AsyncMock(return_value=mock_sandbox)),
        patch.object(server, "make_model", return_value=MagicMock()),
        patch.object(server, "construct_system_prompt", return_value="prompt"),
        patch.object(server, "create_deep_agent", return_value=dummy_agent) as mock_create_deep_agent,
        patch.dict(server.SANDBOX_BACKENDS, {"thread-123": mock_sandbox}, clear=True),
        patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
    ):
        agent = asyncio.run(server.get_agent(config))

    create_kwargs = mock_create_deep_agent.call_args.kwargs
    tool_names = {tool.__name__ for tool in create_kwargs["tools"]}
    middleware = create_kwargs["middleware"]

    assert agent is dummy_agent
    assert config["metadata"]["github_token_encrypted"] == "enc"
    assert config["metadata"]["repo_full_name"] == "langchain-ai/open-swe"
    assert isinstance(config["metadata"]["repo_memory_runtime"], RepoMemoryRuntime)
    assert config["metadata"]["repo_memory_runtime"].repo == "langchain-ai/open-swe"
    assert "remember_repo_decision" in tool_names
    assert "search_similar_code" in tool_names
    assert "get_entity_history" in tool_names
    assert any(type(item).__name__ == "RepoMemoryToolMiddleware" for item in middleware)
    assert any(
        getattr(item, "__name__", "") == "inject_repo_memory_before_model"
        for item in middleware
    )
