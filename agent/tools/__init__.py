from __future__ import annotations

from importlib import import_module


def _optional_attr(module_name: str, attr_name: str):
    try:
        module = import_module(module_name, package=__name__)
    except ModuleNotFoundError:
        return None
    return getattr(module, attr_name)


commit_and_open_pr = _optional_attr(".commit_and_open_pr", "commit_and_open_pr")
fetch_url = _optional_attr(".fetch_url", "fetch_url")
get_entity_history = _optional_attr(".get_entity_history", "get_entity_history")
get_branch_name = _optional_attr(".get_branch_name", "get_branch_name")
get_pr_review_comments = _optional_attr(".get_pr_review_comments", "get_pr_review_comments")
github_comment = _optional_attr(".github_comment", "github_comment")
http_request = _optional_attr(".http_request", "http_request")
linear_comment = _optional_attr(".linear_comment", "linear_comment")
linear_create_issue = _optional_attr(".linear_create_issue", "linear_create_issue")
linear_delete_issue = _optional_attr(".linear_delete_issue", "linear_delete_issue")
linear_get_issue = _optional_attr(".linear_get_issue", "linear_get_issue")
linear_get_issue_comments = _optional_attr(
    ".linear_get_issue_comments",
    "linear_get_issue_comments",
)
linear_list_teams = _optional_attr(".linear_list_teams", "linear_list_teams")
linear_update_issue = _optional_attr(".linear_update_issue", "linear_update_issue")
list_repos = _optional_attr(".list_repos", "list_repos")
remember_repo_decision = _optional_attr(".remember_repo_decision", "remember_repo_decision")
search_similar_code = _optional_attr(".search_similar_code", "search_similar_code")
slack_read_thread_messages = _optional_attr(
    ".slack_read_thread_messages", "slack_read_thread_messages"
)
slack_thread_reply = _optional_attr(".slack_thread_reply", "slack_thread_reply")
submit_pr_review = _optional_attr(".github_review", "submit_pr_review")
create_pr_review = _optional_attr(".github_review", "create_pr_review")
dismiss_pr_review = _optional_attr(".github_review", "dismiss_pr_review")
get_pr_review = _optional_attr(".github_review", "get_pr_review")
list_pr_review_comments = _optional_attr(".github_review", "list_pr_review_comments")
list_pr_reviews = _optional_attr(".github_review", "list_pr_reviews")
update_pr_review = _optional_attr(".github_review", "update_pr_review")
web_search = _optional_attr(".web_search", "web_search")

__all__ = [
    "commit_and_open_pr",
    "create_pr_review",
    "dismiss_pr_review",
    "fetch_url",
    "get_branch_name",
    "get_entity_history",
    "get_pr_review",
    "get_pr_review_comments",
    "github_comment",
    "http_request",
    "linear_comment",
    "list_pr_review_comments",
    "list_pr_reviews",
    "linear_create_issue",
    "linear_delete_issue",
    "linear_get_issue",
    "linear_get_issue_comments",
    "linear_list_teams",
    "linear_update_issue",
    "list_repos",
    "remember_repo_decision",
    "search_similar_code",
    "slack_read_thread_messages",
    "slack_thread_reply",
    "submit_pr_review",
    "update_pr_review",
    "web_search",
]
