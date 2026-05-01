import asyncio
from typing import Any

from ..utils.jira import get_jira_issue


def jira_get_issue(issue_key: str) -> dict[str, Any]:
    """Get a Jira issue by key or ID."""
    return asyncio.run(get_jira_issue(issue_key))
