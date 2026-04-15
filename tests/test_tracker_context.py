from agent.utils.tracker_context import TrackerContext, resolve_tracker_context


def test_generic_tracker_block_wins_over_legacy_provider_data() -> None:
    context = resolve_tracker_context(
        {
            "tracker": {
                "source": "jira",
                "issue_id": "jira-1",
                "issue_ref": "OPS-42",
                "issue_title": "Investigate incident",
                "issue_url": "https://example.atlassian.net/browse/OPS-42",
                "reply_tool_name": "jira_comment",
                "triggering_user_name": "Jules",
            },
            "linear_issue": {
                "id": "linear-1",
                "identifier": "ABC-123",
                "title": "Legacy issue",
                "url": "https://linear.app/issue/ABC-123",
                "triggering_user_name": "Lin",
            },
            "source": "github",
        }
    )

    assert context == TrackerContext(
        source="jira",
        issue_id="jira-1",
        issue_ref="OPS-42",
        issue_title="Investigate incident",
        issue_url="https://example.atlassian.net/browse/OPS-42",
        reply_tool_name="jira_comment",
        triggering_user_name="Jules",
    )


def test_linear_identifier_maps_to_issue_ref() -> None:
    context = resolve_tracker_context(
        {
            "linear_issue": {
                "id": "linear-1",
                "identifier": "ABC-123",
                "title": "Fix auth flow",
                "url": "https://linear.app/acme/issue/ABC-123",
                "triggering_user_name": "Ada",
            }
        }
    )

    assert context == TrackerContext(
        source="linear",
        issue_id="linear-1",
        issue_ref="ABC-123",
        issue_title="Fix auth flow",
        issue_url="https://linear.app/acme/issue/ABC-123",
        reply_tool_name="linear_comment",
        triggering_user_name="Ada",
    )


def test_linear_project_and_number_fallback_builds_issue_ref() -> None:
    context = resolve_tracker_context(
        {
            "linear_issue": {
                "linear_project_id": "ENG",
                "linear_issue_number": 77,
            }
        }
    )

    assert context.issue_ref == "ENG-77"
    assert context.reply_tool_name == "linear_comment"


def test_slack_source_uses_slack_reply_tool() -> None:
    context = resolve_tracker_context(
        {
            "source": "slack",
            "slack_thread": {"triggering_user_name": "Morgan"},
        }
    )

    assert context == TrackerContext(
        source="slack",
        reply_tool_name="slack_thread_reply",
        triggering_user_name="Morgan",
    )


def test_github_source_uses_github_reply_tool() -> None:
    context = resolve_tracker_context({"source": "github"})

    assert context == TrackerContext(
        source="github",
        reply_tool_name="github_comment",
    )


def test_unknown_source_returns_safe_defaults() -> None:
    assert resolve_tracker_context({"source": "something-else"}) == TrackerContext()
