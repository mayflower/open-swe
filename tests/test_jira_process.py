from __future__ import annotations

import asyncio
from typing import Any

from agent import webapp


class _FakeRunsClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.listed: list[dict[str, Any]] = []

    async def create(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.created.append(
            {
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                **kwargs,
            }
        )
        return {"run_id": "run-created"}

    async def list(self, thread_id: str, limit: int = 1) -> list[dict[str, Any]]:
        self.listed.append({"thread_id": thread_id, "limit": limit})
        return [{"run_id": "run-active"}]


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.runs = _FakeRunsClient()


def _issue_data() -> dict[str, Any]:
    return {
        "id": "10001",
        "key": "OPS-42",
        "site": "example.atlassian.net",
        "url": "https://example.atlassian.net/browse/OPS-42",
        "triggering_comment_id": "comment-1",
        "triggering_comment": {
            "id": "comment-1",
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "@openswe please handle this"}],
                    }
                ],
            },
            "author": {
                "displayName": "Ada Lovelace",
            },
        },
        "comment_author": {"displayName": "Ada Lovelace"},
    }


def _full_issue() -> dict[str, Any]:
    return {
        "id": "10001",
        "key": "OPS-42",
        "fields": {
            "summary": "Fix flaky test",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "The test fails intermittently."}],
                    }
                ],
            },
            "comment": {
                "comments": [
                    {
                        "id": "comment-0",
                        "created": "2026-04-14T10:00:00.000+0000",
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Earlier context"}],
                                }
                            ],
                        },
                        "author": {"displayName": "Grace Hopper"},
                    },
                    {
                        "id": "comment-1",
                        "created": "2026-04-15T10:00:00.000+0000",
                        "body": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "@openswe please handle this"}],
                                }
                            ],
                        },
                        "author": {"displayName": "Ada Lovelace"},
                    },
                ]
            },
            "reporter": {"displayName": "Ada Lovelace"},
        },
    }


def test_process_jira_issue_creates_run_for_idle_thread(monkeypatch) -> None:
    fake_client = _FakeLangGraphClient()
    trace_calls: list[dict[str, Any]] = []

    async def fake_get_jira_issue(issue_id_or_key: str) -> dict[str, Any]:
        return {"issue": _full_issue()}

    async def fake_is_thread_active(thread_id: str) -> bool:
        return False

    async def fake_post_jira_trace_comment(issue_id_or_key: str, run_id: str) -> None:
        trace_calls.append({"issue": issue_id_or_key, "run_id": run_id})

    monkeypatch.setattr(webapp, "get_jira_issue", fake_get_jira_issue)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "post_jira_trace_comment", fake_post_jira_trace_comment)
    monkeypatch.setattr(webapp, "get_client", lambda url=None: fake_client)

    asyncio.run(
        webapp.process_jira_issue(_issue_data(), {"owner": "langchain-ai", "name": "open-swe"})
    )

    created = fake_client.runs.created[0]
    configurable = created["config"]["configurable"]
    prompt = created["input"]["messages"][0]["content"]

    assert created["thread_id"] == webapp.generate_thread_id_from_jira_issue(
        "example.atlassian.net", "10001"
    )
    assert configurable["source"] == "jira"
    assert configurable["tracker"]["issue_ref"] == "OPS-42"
    assert configurable["tracker"]["reply_tool_name"] == "jira_comment"
    assert configurable["user_email"] is None
    assert "The test fails intermittently." in prompt
    assert "@openswe please handle this" in prompt
    assert trace_calls == [{"issue": "OPS-42", "run_id": "run-created"}]


def test_process_jira_issue_queues_followup_for_busy_thread(monkeypatch) -> None:
    fake_client = _FakeLangGraphClient()
    queued: list[dict[str, Any]] = []
    trace_calls: list[dict[str, Any]] = []

    async def fake_get_jira_issue(issue_id_or_key: str) -> dict[str, Any]:
        return {"issue": _full_issue()}

    async def fake_is_thread_active(thread_id: str) -> bool:
        return True

    async def fake_queue_message_for_thread(thread_id: str, message_content: Any) -> bool:
        queued.append({"thread_id": thread_id, "content": message_content})
        return True

    async def fake_post_jira_trace_comment(issue_id_or_key: str, run_id: str) -> None:
        trace_calls.append({"issue": issue_id_or_key, "run_id": run_id})

    monkeypatch.setattr(webapp, "get_jira_issue", fake_get_jira_issue)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(webapp, "post_jira_trace_comment", fake_post_jira_trace_comment)
    monkeypatch.setattr(webapp, "get_client", lambda url=None: fake_client)

    asyncio.run(
        webapp.process_jira_issue(_issue_data(), {"owner": "langchain-ai", "name": "open-swe"})
    )

    assert fake_client.runs.created == []
    assert queued
    assert "@openswe please handle this" in queued[0]["content"]
    assert trace_calls == [{"issue": "OPS-42", "run_id": "run-active"}]


def test_process_jira_issue_keeps_user_email_when_present(monkeypatch) -> None:
    fake_client = _FakeLangGraphClient()
    full_issue = _full_issue()
    full_issue["fields"]["reporter"]["emailAddress"] = "ada@example.com"

    async def fake_get_jira_issue(issue_id_or_key: str) -> dict[str, Any]:
        return {"issue": full_issue}

    async def fake_is_thread_active(thread_id: str) -> bool:
        return False

    async def fake_post_jira_trace_comment(issue_id_or_key: str, run_id: str) -> None:
        return None

    monkeypatch.setattr(webapp, "get_jira_issue", fake_get_jira_issue)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "post_jira_trace_comment", fake_post_jira_trace_comment)
    monkeypatch.setattr(webapp, "get_client", lambda url=None: fake_client)

    asyncio.run(
        webapp.process_jira_issue(_issue_data(), {"owner": "langchain-ai", "name": "open-swe"})
    )

    configurable = fake_client.runs.created[0]["config"]["configurable"]
    assert configurable["user_email"] == "ada@example.com"
