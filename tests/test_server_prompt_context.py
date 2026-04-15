import importlib


def _import_server():
    return importlib.import_module("agent.server")


def test_build_prompt_context_uses_linear_legacy_fields() -> None:
    server = _import_server()

    context = server.build_prompt_context(
        {
            "linear_issue": {
                "identifier": "ABC-123",
                "linear_project_id": "ABC",
                "linear_issue_number": "123",
            }
        }
    )

    assert context == {
        "source": "linear",
        "reply_tool_name": "linear_comment",
        "issue_ref": "ABC-123",
    }


def test_build_prompt_context_prefers_generic_tracker_block() -> None:
    server = _import_server()

    context = server.build_prompt_context(
        {
            "tracker": {
                "source": "jira",
                "reply_tool_name": "jira_comment",
                "issue_ref": "OPS-42",
            },
            "linear_issue": {
                "identifier": "ABC-123",
            },
        }
    )

    assert context == {
        "source": "jira",
        "reply_tool_name": "jira_comment",
        "issue_ref": "OPS-42",
    }
