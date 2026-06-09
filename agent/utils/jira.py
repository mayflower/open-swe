"""Jira Cloud REST helpers."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .jira_adf import text_to_adf
from .langsmith import get_langsmith_trace_url


def _jira_config() -> dict[str, str]:
    base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    api_email = os.environ.get("JIRA_API_EMAIL", "")
    api_token = os.environ.get("JIRA_API_TOKEN", "")

    missing = [
        name
        for name, value in (
            ("JIRA_BASE_URL", base_url),
            ("JIRA_API_EMAIL", api_email),
            ("JIRA_API_TOKEN", api_token),
        )
        if not value
    ]
    if missing:
        return {"error": f"Missing Jira configuration: {', '.join(missing)}"}

    return {
        "base_url": base_url,
        "api_email": api_email,
        "api_token": api_token,
    }


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _jira_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _jira_config()
    if "error" in config:
        return config

    url = f"{config['base_url']}{path}"
    auth = httpx.BasicAuth(config["api_email"], config["api_token"])

    async with httpx.AsyncClient(auth=auth, headers=_headers()) as http_client:
        try:
            response = await http_client.request(method, url, json=json_body)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            return {"error": f"Jira API {exc.response.status_code} error for {path}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


async def get_jira_issue(issue_id_or_key: str) -> dict[str, Any]:
    """Get a Jira issue by ID or key."""
    result = await _jira_request("GET", f"/rest/api/3/issue/{issue_id_or_key}")
    if "error" in result:
        return result
    return {"issue": result}


async def get_jira_issue_comments(issue_id_or_key: str) -> list[dict[str, Any]] | dict[str, Any]:
    """Get Jira issue comments."""
    result = await _jira_request("GET", f"/rest/api/3/issue/{issue_id_or_key}/comment")
    if "error" in result:
        return result
    comments = result.get("comments", [])
    return comments if isinstance(comments, list) else []


async def comment_on_jira_issue(issue_id_or_key: str, adf_body: dict[str, Any]) -> bool:
    """Post an ADF comment to a Jira issue."""
    result = await _jira_request(
        "POST",
        f"/rest/api/3/issue/{issue_id_or_key}/comment",
        json_body={"body": adf_body},
    )
    return "error" not in result


async def post_jira_trace_comment(issue_id_or_key: str, run_id: str) -> None:
    """Post a short trace comment on a Jira issue when available."""
    trace_url = get_langsmith_trace_url(run_id)
    if trace_url:
        await comment_on_jira_issue(issue_id_or_key, text_to_adf(f"On it! {trace_url}"))
