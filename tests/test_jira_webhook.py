from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent import webapp
from agent.utils.repo import extract_repo_from_text
from agent.webapp import (
    generate_thread_id_from_jira_issue,
    get_repo_config_from_jira_project_mapping,
    normalize_jira_comment_body,
    verify_jira_signature,
)


def test_generate_thread_id_from_jira_issue_is_deterministic() -> None:
    first = generate_thread_id_from_jira_issue("example.atlassian.net", "10001")
    second = generate_thread_id_from_jira_issue("example.atlassian.net", "10001")

    assert first == second
    assert len(first) == 36


def test_generate_thread_id_from_jira_issue_differs_by_site() -> None:
    first = generate_thread_id_from_jira_issue("site-a.atlassian.net", "10001")
    second = generate_thread_id_from_jira_issue("site-b.atlassian.net", "10001")

    assert first != second


def test_verify_jira_signature_accepts_valid_hmac_sha256() -> None:
    body = b'{"issue":{"id":"10001"}}'
    secret = "jira-secret"
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_jira_signature(body, signature, secret) is True
    assert verify_jira_signature(body, "invalid", secret) is False


def test_explicit_repo_override_beats_project_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.webapp.JIRA_PROJECT_TO_REPO",
        {"OPS": {"owner": "langchain-ai", "name": "open-swe"}},
    )

    comment_text = normalize_jira_comment_body(
        {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "@openswe please use repo:custom-org/custom-repo"}],
                }
            ],
        }
    )

    explicit_repo = extract_repo_from_text(comment_text)
    fallback_repo = get_repo_config_from_jira_project_mapping("OPS")

    assert explicit_repo == {"owner": "custom-org", "name": "custom-repo"}
    assert fallback_repo == {"owner": "langchain-ai", "name": "open-swe"}


def test_project_mapping_fallback_works_without_explicit_repo(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.webapp.JIRA_PROJECT_TO_REPO",
        {"OPS": {"owner": "langchain-ai", "name": "open-swe"}},
    )

    comment_text = normalize_jira_comment_body(
        {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "@openswe please take a look"}],
                }
            ],
        }
    )

    assert extract_repo_from_text(comment_text) is None
    assert get_repo_config_from_jira_project_mapping("OPS") == {
        "owner": "langchain-ai",
        "name": "open-swe",
    }


def test_normalize_jira_comment_body_converts_adf_to_text() -> None:
    body = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "mention", "attrs": {"text": "@openswe"}},
                    {"type": "text", "text": " please use repo:langchain-ai/open-swe"},
                ],
            }
        ],
    }

    assert normalize_jira_comment_body(body) == "@openswe please use repo:langchain-ai/open-swe"


def _sign_jira_body(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _jira_payload(comment_text: str = "@openswe please take a look") -> dict:
    return {
        "webhookEvent": "comment_created",
        "baseUrl": "https://example.atlassian.net",
        "comment": {
            "id": "comment-1",
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment_text}],
                    }
                ],
            },
            "author": {
                "accountId": "user-1",
                "displayName": "Ada Lovelace",
                "emailAddress": "ada@example.com",
            },
        },
        "issue": {
            "id": "10001",
            "key": "OPS-42",
            "fields": {
                "summary": "Fix flaky test",
                "project": {"key": "OPS"},
            },
        },
    }


@pytest.mark.asyncio
async def test_jira_webhook_invalid_signature_is_rejected() -> None:
    mock_request = AsyncMock()
    mock_request.body.return_value = json.dumps(_jira_payload()).encode()
    mock_request.headers = {"X-Hub-Signature": "sha256=invalid"}

    with pytest.raises(HTTPException, match="Invalid Jira webhook signature"):
        await webapp.jira_webhook(mock_request, AsyncMock())


@pytest.mark.asyncio
async def test_jira_webhook_ignores_non_comment_created_events() -> None:
    body = json.dumps({**_jira_payload(), "webhookEvent": "jira:issue_updated"}).encode()
    mock_request = AsyncMock()
    mock_request.body.return_value = body
    mock_request.headers = {"X-Hub-Signature": _sign_jira_body(body, "jira-secret")}

    with patch.object(webapp, "JIRA_WEBHOOK_SECRET", "jira-secret"):
        response = await webapp.jira_webhook(mock_request, AsyncMock())

    assert response["status"] == "ignored"


@pytest.mark.asyncio
async def test_jira_webhook_ignores_missing_openswe_mention() -> None:
    payload = _jira_payload("please take a look")
    body = json.dumps(payload).encode()
    mock_request = AsyncMock()
    mock_request.body.return_value = body
    mock_request.headers = {"X-Hub-Signature": _sign_jira_body(body, "jira-secret")}

    with patch.object(webapp, "JIRA_WEBHOOK_SECRET", "jira-secret"):
        response = await webapp.jira_webhook(mock_request, AsyncMock())

    assert response["status"] == "ignored"


@pytest.mark.asyncio
async def test_jira_webhook_ignores_configured_bot_account() -> None:
    payload = _jira_payload()
    body = json.dumps(payload).encode()
    mock_request = AsyncMock()
    mock_request.body.return_value = body
    mock_request.headers = {"X-Hub-Signature": _sign_jira_body(body, "jira-secret")}

    with (
        patch.object(webapp, "JIRA_WEBHOOK_SECRET", "jira-secret"),
        patch.object(webapp, "JIRA_BOT_ACCOUNT_ID", "user-1"),
    ):
        response = await webapp.jira_webhook(mock_request, AsyncMock())

    assert response["status"] == "ignored"


@pytest.mark.asyncio
async def test_jira_webhook_accepts_valid_comment_and_schedules_processing() -> None:
    payload = _jira_payload("@openswe please use repo:custom-org/custom-repo")
    body = json.dumps(payload).encode()
    mock_request = AsyncMock()
    mock_request.body.return_value = body
    mock_request.headers = {"X-Hub-Signature": _sign_jira_body(body, "jira-secret")}
    background_tasks = Mock()

    with (
        patch.object(webapp, "JIRA_WEBHOOK_SECRET", "jira-secret"),
        patch.object(webapp, "_is_repo_org_allowed", return_value=True),
        patch.object(
            webapp,
            "JIRA_PROJECT_TO_REPO",
            {"OPS": {"owner": "langchain-ai", "name": "open-swe"}},
        ),
    ):
        response = await webapp.jira_webhook(mock_request, background_tasks)

    assert response["status"] == "accepted"
    assert "custom-org/custom-repo" in response["message"]
    args = background_tasks.add_task.call_args[0]
    assert args[0] is webapp.process_jira_issue
    assert args[1]["key"] == "OPS-42"
    assert args[1]["site"] == "https://example.atlassian.net"
    assert args[2] == {"owner": "custom-org", "name": "custom-repo"}


@pytest.mark.asyncio
async def test_jira_webhook_uses_project_mapping_fallback() -> None:
    payload = _jira_payload("@openswe please handle this")
    body = json.dumps(payload).encode()
    mock_request = AsyncMock()
    mock_request.body.return_value = body
    mock_request.headers = {"X-Hub-Signature": _sign_jira_body(body, "jira-secret")}
    background_tasks = Mock()

    with (
        patch.object(webapp, "JIRA_WEBHOOK_SECRET", "jira-secret"),
        patch.object(webapp, "_is_repo_org_allowed", return_value=True),
        patch.object(
            webapp,
            "JIRA_PROJECT_TO_REPO",
            {"OPS": {"owner": "langchain-ai", "name": "open-swe"}},
        ),
    ):
        response = await webapp.jira_webhook(mock_request, background_tasks)

    assert response["status"] == "accepted"
    assert "langchain-ai/open-swe" in response["message"]
    assert background_tasks.add_task.call_args[0][2] == {
        "owner": "langchain-ai",
        "name": "open-swe",
    }


def test_jira_webhook_get_endpoint_returns_health_response() -> None:
    client = TestClient(webapp.app)
    response = client.get("/webhooks/jira")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
