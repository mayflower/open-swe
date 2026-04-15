from __future__ import annotations

import importlib
from typing import Any

from agent.utils.jira_adf import adf_to_text


def test_jira_comment_calls_jira_utility_with_adf(monkeypatch) -> None:
    jira_comment_module = importlib.import_module("agent.tools.jira_comment")

    captured: dict[str, Any] = {}

    async def fake_comment_on_jira_issue(issue_key: str, adf_body: dict[str, Any]) -> bool:
        captured["issue_key"] = issue_key
        captured["adf_body"] = adf_body
        return True

    monkeypatch.setattr(jira_comment_module, "comment_on_jira_issue", fake_comment_on_jira_issue)

    result = jira_comment_module.jira_comment("Working on it", "OPS-42")

    assert result == {"success": True}
    assert captured["issue_key"] == "OPS-42"
    assert adf_to_text(captured["adf_body"]) == "Working on it"


def test_jira_get_issue_delegates_to_jira_utility(monkeypatch) -> None:
    jira_get_issue_module = importlib.import_module("agent.tools.jira_get_issue")

    async def fake_get_jira_issue(issue_key: str) -> dict[str, Any]:
        return {"issue": {"key": issue_key}}

    monkeypatch.setattr(jira_get_issue_module, "get_jira_issue", fake_get_jira_issue)

    assert jira_get_issue_module.jira_get_issue("OPS-42") == {"issue": {"key": "OPS-42"}}


def test_jira_get_issue_comments_delegates_to_jira_utility(monkeypatch) -> None:
    jira_get_issue_comments_module = importlib.import_module("agent.tools.jira_get_issue_comments")

    async def fake_get_jira_issue_comments(issue_key: str) -> list[dict[str, Any]]:
        return [{"id": "c1", "issue_key": issue_key}]

    monkeypatch.setattr(
        jira_get_issue_comments_module,
        "get_jira_issue_comments",
        fake_get_jira_issue_comments,
    )

    assert jira_get_issue_comments_module.jira_get_issue_comments("OPS-42") == {
        "comments": [{"id": "c1", "issue_key": "OPS-42"}]
    }
