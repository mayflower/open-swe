from __future__ import annotations

import importlib

from agent.webapp import build_linear_run_configurable


def _import_server():
    return importlib.import_module("agent.server")


def _tool_names(tools: list[object]) -> set[str]:
    return {getattr(tool, "__name__", "") for tool in tools}


def test_get_tools_for_source_linear_includes_linear_tools_only() -> None:
    server = _import_server()

    tool_names = _tool_names(server.get_tools_for_source("linear"))

    assert "linear_comment" in tool_names
    assert "linear_get_issue" in tool_names
    assert "jira_comment" not in tool_names
    assert "jira_get_issue" not in tool_names


def test_get_tools_for_source_jira_includes_jira_tools_only() -> None:
    server = _import_server()

    tool_names = _tool_names(server.get_tools_for_source("jira"))

    assert "jira_comment" in tool_names
    assert "jira_get_issue" in tool_names
    assert "linear_comment" not in tool_names
    assert "linear_get_issue" not in tool_names


def test_build_prompt_context_is_driven_by_tracker_block() -> None:
    server = _import_server()

    context = server.build_prompt_context(
        {
            "tracker": {
                "source": "jira",
                "reply_tool_name": "jira_comment",
                "issue_ref": "OPS-42",
            },
            "linear_issue": {
                "identifier": "ABC-123",
            },
        }
    )

    assert context == {
        "source": "jira",
        "reply_tool_name": "jira_comment",
        "issue_ref": "OPS-42",
    }


def test_build_linear_run_configurable_includes_generic_tracker_block() -> None:
    configurable = build_linear_run_configurable(
        {"owner": "langchain-ai", "name": "open-swe"},
        "issue-123",
        "Fix flaky test",
        "https://linear.app/example/issue/ABC-123",
        "ABC-123",
        "Ada Lovelace",
        "ada@example.com",
    )

    assert configurable["source"] == "linear"
    assert configurable["linear_issue"]["identifier"] == "ABC-123"
    assert configurable["tracker"] == {
        "source": "linear",
        "issue_id": "issue-123",
        "issue_ref": "ABC-123",
        "issue_title": "Fix flaky test",
        "issue_url": "https://linear.app/example/issue/ABC-123",
        "reply_tool_name": "linear_comment",
        "triggering_user_name": "Ada Lovelace",
    }
