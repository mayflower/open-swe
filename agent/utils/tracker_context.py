from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrackerContext:
    source: str = ""
    issue_id: str = ""
    issue_ref: str = ""
    issue_title: str = ""
    issue_url: str = ""
    reply_tool_name: str = ""
    triggering_user_name: str = ""


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _build_linear_issue_ref(linear_issue: Mapping[str, Any]) -> str:
    identifier = _string_value(linear_issue.get("identifier"))
    if identifier:
        return identifier

    project_id = _string_value(linear_issue.get("linear_project_id"))
    issue_number = linear_issue.get("linear_issue_number")
    issue_number_str = str(issue_number) if issue_number not in (None, "") else ""
    if project_id and issue_number_str:
        return f"{project_id}-{issue_number_str}"

    return ""


def resolve_tracker_context(configurable: Mapping[str, Any]) -> TrackerContext:
    tracker = configurable.get("tracker")
    if isinstance(tracker, Mapping):
        return TrackerContext(
            source=_string_value(tracker.get("source")),
            issue_id=_string_value(tracker.get("issue_id")),
            issue_ref=_string_value(tracker.get("issue_ref")),
            issue_title=_string_value(tracker.get("issue_title")),
            issue_url=_string_value(tracker.get("issue_url")),
            reply_tool_name=_string_value(tracker.get("reply_tool_name")),
            triggering_user_name=_string_value(tracker.get("triggering_user_name")),
        )

    linear_issue = configurable.get("linear_issue")
    if isinstance(linear_issue, Mapping):
        return TrackerContext(
            source="linear",
            issue_id=_string_value(linear_issue.get("id")),
            issue_ref=_build_linear_issue_ref(linear_issue),
            issue_title=_string_value(linear_issue.get("title")),
            issue_url=_string_value(linear_issue.get("url")),
            reply_tool_name="linear_comment",
            triggering_user_name=_string_value(linear_issue.get("triggering_user_name")),
        )

    source = _string_value(configurable.get("source"))
    if source == "slack":
        slack_thread = configurable.get("slack_thread")
        triggering_user_name = ""
        if isinstance(slack_thread, Mapping):
            triggering_user_name = _string_value(slack_thread.get("triggering_user_name"))
        return TrackerContext(
            source="slack",
            reply_tool_name="slack_thread_reply",
            triggering_user_name=triggering_user_name,
        )

    if source == "github":
        return TrackerContext(
            source="github",
            reply_tool_name="github_comment",
        )

    return TrackerContext()
