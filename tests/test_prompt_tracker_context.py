from agent.prompt import construct_system_prompt


def test_construct_system_prompt_uses_linear_reply_tool_and_issue_ref() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace",
        source="linear",
        reply_tool_name="linear_comment",
        issue_ref="ABC-123",
    )

    assert "linear_comment" in prompt
    assert "[closes ABC-123]" in prompt


def test_construct_system_prompt_supports_jira_reply_tool_and_issue_ref() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace",
        source="jira",
        reply_tool_name="jira_comment",
        issue_ref="OPS-42",
    )

    assert "jira_comment" in prompt
    assert "[closes OPS-42]" in prompt


def test_construct_system_prompt_preserves_slack_format_guidance() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace",
        source="slack",
        reply_tool_name="slack_thread_reply",
        issue_ref="SLK-9",
    )

    assert "Format messages using Slack's mrkdwn format" in prompt
    assert "Do NOT use **bold**, [link](url), or other standard Markdown syntax." in prompt


def test_construct_system_prompt_uses_placeholder_for_empty_issue_ref() -> None:
    prompt = construct_system_prompt(
        working_dir="/workspace",
        source="jira",
        reply_tool_name="jira_comment",
        issue_ref="",
    )

    assert "[closes <ISSUE_REF>]" in prompt
