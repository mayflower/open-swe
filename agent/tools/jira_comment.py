import asyncio
from typing import Any

from ..utils.jira import comment_on_jira_issue
from ..utils.jira_adf import text_to_adf


def jira_comment(comment_body: str, issue_key: str) -> dict[str, Any]:
    """Post a comment to a Jira issue."""
    success = asyncio.run(comment_on_jira_issue(issue_key, text_to_adf(comment_body)))
    return {"success": success}
