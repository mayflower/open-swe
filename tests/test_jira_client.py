from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent.utils.jira_adf import adf_to_text, text_to_adf


class _FakeAsyncClient:
    def __init__(self, *, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def _json_response(method: str, url: str, payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(method, url),
    )


@pytest.mark.asyncio
async def test_missing_env_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.utils import jira

    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_API_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)

    result = await jira.get_jira_issue("OPS-42")

    assert "error" in result
    assert "JIRA_BASE_URL" in result["error"]


@pytest.mark.asyncio
async def test_get_jira_issue_uses_rest_v3_issue_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.utils import jira

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_API_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    fake_client = _FakeAsyncClient(
        response=_json_response(
            "GET",
            "https://example.atlassian.net/rest/api/3/issue/OPS-42",
            {"id": "1001", "key": "OPS-42"},
        )
    )
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kwargs: fake_client)

    result = await jira.get_jira_issue("OPS-42")

    assert result == {"issue": {"id": "1001", "key": "OPS-42"}}
    assert fake_client.calls[0]["method"] == "GET"
    assert fake_client.calls[0]["url"].endswith("/rest/api/3/issue/OPS-42")


@pytest.mark.asyncio
async def test_get_jira_issue_comments_uses_comments_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.utils import jira

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_API_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    fake_client = _FakeAsyncClient(
        response=_json_response(
            "GET",
            "https://example.atlassian.net/rest/api/3/issue/OPS-42/comment",
            {"comments": [{"id": "c1"}, {"id": "c2"}]},
        )
    )
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kwargs: fake_client)

    result = await jira.get_jira_issue_comments("OPS-42")

    assert result == [{"id": "c1"}, {"id": "c2"}]
    assert fake_client.calls[0]["url"].endswith("/rest/api/3/issue/OPS-42/comment")


@pytest.mark.asyncio
async def test_comment_on_jira_issue_posts_adf_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.utils import jira

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_API_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    fake_client = _FakeAsyncClient(
        response=_json_response(
            "POST",
            "https://example.atlassian.net/rest/api/3/issue/OPS-42/comment",
            {"id": "comment-1"},
            status_code=201,
        )
    )
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kwargs: fake_client)

    adf_body = text_to_adf("On it!")
    success = await jira.comment_on_jira_issue("OPS-42", adf_body)

    assert success is True
    assert fake_client.calls[0]["json"] == {"body": adf_body}


@pytest.mark.asyncio
async def test_post_jira_trace_comment_uses_trace_url_and_posts_only_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.utils import jira

    captured: dict[str, Any] = {}

    async def fake_comment_on_jira_issue(issue_id_or_key: str, adf_body: dict[str, Any]) -> bool:
        captured["issue"] = issue_id_or_key
        captured["body"] = adf_body
        return True

    monkeypatch.setattr(jira, "comment_on_jira_issue", fake_comment_on_jira_issue)
    monkeypatch.setattr(jira, "get_langsmith_trace_url", lambda run_id: f"https://smith/{run_id}")

    await jira.post_jira_trace_comment("OPS-42", "run-123")

    assert captured["issue"] == "OPS-42"
    assert "On it!" in adf_to_text(captured["body"])
    assert "https://smith/run-123" in adf_to_text(captured["body"])

    captured.clear()
    monkeypatch.setattr(jira, "get_langsmith_trace_url", lambda run_id: None)
    await jira.post_jira_trace_comment("OPS-42", "run-456")
    assert captured == {}


@pytest.mark.asyncio
async def test_http_errors_are_handled_predictably(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.utils import jira

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_API_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    fake_client = _FakeAsyncClient(error=httpx.HTTPError("boom"))
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kwargs: fake_client)

    result = await jira.get_jira_issue("OPS-42")

    assert result == {"error": "boom"}


@pytest.mark.asyncio
async def test_http_status_errors_are_returned_as_structured_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.utils import jira

    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_API_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    fake_client = _FakeAsyncClient(
        response=_json_response(
            "GET",
            "https://example.atlassian.net/rest/api/3/issue/OPS-404",
            {"errorMessages": ["Issue does not exist"]},
            status_code=404,
        )
    )
    monkeypatch.setattr(jira.httpx, "AsyncClient", lambda **kwargs: fake_client)

    result = await jira.get_jira_issue("OPS-404")

    assert "error" in result
    assert "404" in result["error"]
