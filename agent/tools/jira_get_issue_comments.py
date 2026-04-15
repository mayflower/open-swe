import asyncio
from typing import Any

from ..utils.jira import get_jira_issue_comments


def jira_get_issue_comments(issue_key: str) -> dict[str, Any]:
    """Get comments on a Jira issue."""
    result = asyncio.run(get_jira_issue_comments(issue_key))
    if isinstance(result, dict):
        return result
    return {"comments": result}
