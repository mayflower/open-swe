"""Main entry point and CLI loop for Open SWE agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
# ruff: noqa: E402
import logging
import os
import warnings
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

# Now safe to import agent (which imports LangChain modules)
from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol

from .middleware import (
    RepoMemoryToolMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    inject_repo_memory_before_model,
    open_pr_if_needed,
)
from .repo_memory.runtime import bind_runtime_context, get_or_create_repo_memory_runtime
from .prompt import construct_system_prompt
from .tools import (
    commit_and_open_pr,
    create_pr_review,
    dismiss_pr_review,
    fetch_url,
    get_branch_name,
    get_entity_history,
    get_pr_review,
    get_pr_review_comments,
    github_comment,
    http_request,
    jira_comment,
    jira_get_issue,
    jira_get_issue_comments,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_update_issue,
    list_pr_review_comments,
    list_pr_reviews,
    list_repos,
    remember_repo_decision,
    search_repo_memory,
    search_similar_code,
    slack_read_thread_messages,
    slack_thread_reply,
    submit_pr_review,
    update_pr_review,
    web_search,
)
from .utils.auth import resolve_github_token
from .utils.github_app import get_github_app_installation_token
from .utils.model import ModelKwargs, OpenAIReasoning, make_model
from .utils.sandbox import create_sandbox
from .utils.sandbox_github_auth import configure_github_network_access
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.tracker_context import resolve_tracker_context

client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.sandbox_state import SANDBOX_BACKENDS, get_sandbox_id_from_metadata


async def _create_sandbox_with_github_access() -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub network access configured."""
    sandbox_backend = await asyncio.to_thread(create_sandbox)

    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type in {"langsmith", "agent_sandbox"}:
        installation_token = await get_github_app_installation_token()
        if not installation_token:
            msg = "Cannot configure GitHub access: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)
        await asyncio.to_thread(
            configure_github_network_access,
            sandbox_backend,
            installation_token,
        )

    return sandbox_backend


async def _refresh_github_access(
    sandbox_backend: SandboxBackendProtocol,
) -> None:
    """Refresh GitHub network credentials for reused sandboxes."""
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type not in {"langsmith", "agent_sandbox"}:
        return

    installation_token = await get_github_app_installation_token()
    if not installation_token:
        logger.warning(
            "Skipping GitHub access refresh for sandbox %s: installation token unavailable",
            sandbox_backend.id,
        )
        return

    await asyncio.to_thread(
        configure_github_network_access,
        sandbox_backend,
        installation_token,
    )


async def _recreate_sandbox(thread_id: str) -> SandboxBackendProtocol:
    """Recreate a sandbox after a connection failure.

    Clears the stale cache entry, sets the SANDBOX_CREATING sentinel,
    and creates a fresh sandbox.
    The agent is responsible for cloning repos via tools.
    """
    SANDBOX_BACKENDS.pop(thread_id, None)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": SANDBOX_CREATING},
    )
    try:
        sandbox_backend = await asyncio.to_thread(create_sandbox)
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
        raise
    return sandbox_backend


async def check_or_recreate_sandbox(
    sandbox_backend: SandboxBackendProtocol, thread_id: str
) -> SandboxBackendProtocol:
    """Check if a cached sandbox is reachable; recreate it if not.

    Pings the sandbox with a lightweight command. If the sandbox is
    unreachable or unhealthy, it is torn down and a fresh one
    is created via _recreate_sandbox.

    Returns the original backend if healthy, or a new one if recreated.
    """
    try:
        result = await asyncio.to_thread(sandbox_backend.execute, "echo ok")
    except Exception:
        logger.warning(
            "Cached sandbox is no longer reachable for thread %s, recreating",
            thread_id,
        )
        sandbox_backend = await _recreate_sandbox(thread_id)
    else:
        if getattr(result, "exit_code", 0) != 0:
            logger.warning(
                "Cached sandbox health check failed for thread %s, recreating",
                thread_id,
            )
            sandbox_backend = await _recreate_sandbox(thread_id)
    return sandbox_backend


async def _wait_for_sandbox_id(thread_id: str) -> str:
    """Wait for sandbox_id to be set in thread metadata.

    Polls thread metadata until sandbox_id is set to a real value
    (not the creating sentinel).

    Raises:
        TimeoutError: If sandbox creation takes too long
    """
    elapsed = 0.0
    while elapsed < SANDBOX_CREATION_TIMEOUT:
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        if sandbox_id is not None and sandbox_id != SANDBOX_CREATING:
            return sandbox_id
        await asyncio.sleep(SANDBOX_POLL_INTERVAL)
        elapsed += SANDBOX_POLL_INTERVAL

    msg = f"Timeout waiting for sandbox creation for thread {thread_id}"
    raise TimeoutError(msg)


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """Check if the graph is loaded for actual execution vs introspection."""
    return (
        config["configurable"].get("__is_for_execution__", False)
        if "configurable" in config
        else False
    )


DEFAULT_LLM_MODEL_ID = "openai:gpt-5.5"
DEFAULT_LLM_REASONING: OpenAIReasoning = {"effort": "medium"}
DEFAULT_LLM_MAX_TOKENS = 64_000
DEFAULT_RECURSION_LIMIT = 9_999

BASE_TOOLS = [
    http_request,
    fetch_url,
    web_search,
    list_repos,
    get_branch_name,
    remember_repo_decision,
    search_similar_code,
    search_repo_memory,
    get_entity_history,
    commit_and_open_pr,
    list_pr_reviews,
    get_pr_review,
    get_pr_review_comments,
    create_pr_review,
    update_pr_review,
    dismiss_pr_review,
    submit_pr_review,
    list_pr_review_comments,
]

LINEAR_TRACKER_TOOLS = [
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_update_issue,
]

JIRA_TRACKER_TOOLS = [
    jira_comment,
    jira_get_issue,
    jira_get_issue_comments,
]

SLACK_REPLY_TOOLS = [slack_thread_reply, slack_read_thread_messages]
GITHUB_REPLY_TOOLS = [github_comment]


def build_prompt_context(configurable: Mapping[str, Any]) -> dict[str, str]:
    tracker_context = resolve_tracker_context(configurable)
    return {
        "source": tracker_context.source,
        "reply_tool_name": tracker_context.reply_tool_name,
        "issue_ref": tracker_context.issue_ref,
    }


def get_tools_for_source(source: str) -> list[Any]:
    """Return the tool surface for a specific tracker or channel source."""
    tools = list(BASE_TOOLS)

    if source == "linear":
        tools.extend(LINEAR_TRACKER_TOOLS)
    elif source == "jira":
        tools.extend(JIRA_TRACKER_TOOLS)
    elif source == "slack":
        tools.extend(SLACK_REPLY_TOOLS)
    elif source == "github":
        tools.extend(GITHUB_REPLY_TOOLS)

    return tools


def build_agent_middleware() -> list[Any]:
    return [
        ToolErrorMiddleware(),
        RepoMemoryToolMiddleware(),
        check_message_queue_before_model,
        inject_repo_memory_before_model,
        ensure_no_empty_msg,
        open_pr_if_needed,
    ]


async def get_agent(config: RunnableConfig) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)
    config.setdefault("metadata", {})

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    github_token, new_encrypted = await resolve_github_token(config, thread_id)
    config["metadata"]["github_token_encrypted"] = new_encrypted

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress, waiting...")
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_backend:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
        await _refresh_github_access(sandbox_backend)

    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})

        try:
            sandbox_backend = await _create_sandbox_with_github_access()
            logger.info("Sandbox created: %s", sandbox_backend.id)
        except Exception:
            logger.exception("Failed to create sandbox")
            try:
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                logger.info("Reset sandbox_id to None for thread %s", thread_id)
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
            logger.info("Connected to existing sandbox %s", sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            # Reset sandbox_id and create a new sandbox with proxy auth configured
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": SANDBOX_CREATING},
            )

            try:
                sandbox_backend = await _create_sandbox_with_github_access()
                logger.info("New sandbox created: %s", sandbox_backend.id)
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise

        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
        await _refresh_github_access(sandbox_backend)

    SANDBOX_BACKENDS[thread_id] = sandbox_backend

    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id,
            metadata={"sandbox_id": sandbox_backend.id},
        )

        await asyncio.to_thread(
            sandbox_backend.execute,
            (
                "git config --global user.name 'open-swe[bot]' && "
                "git config --global user.email 'open-swe@users.noreply.github.com'"
            ),
        )

    tracker_context = resolve_tracker_context(config["configurable"])
    prompt_context = build_prompt_context(config["configurable"])

    repo_config = config["metadata"].get("repo", {})
    repo_full_name = "unknown"
    if isinstance(repo_config, dict) and repo_config.get("owner") and repo_config.get("name"):
        repo_full_name = f"{repo_config['owner']}/{repo_config['name']}"
    repo_memory_runtime = get_or_create_repo_memory_runtime(repo_full_name)
    config["metadata"]["repo_full_name"] = repo_full_name
    config["metadata"]["repo_memory_runtime"] = repo_memory_runtime

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
    bind_runtime_context(
        repo_memory_runtime,
        sandbox_backend=sandbox_backend,
        work_dir=work_dir,
    )

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id == DEFAULT_LLM_MODEL_ID:
        model_kwargs["reasoning"] = DEFAULT_LLM_REASONING

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=construct_system_prompt(
            working_dir=work_dir,
            **prompt_context,
        ),
        tools=get_tools_for_source(tracker_context.source),
        backend=sandbox_backend,
        middleware=build_agent_middleware(),
    ).with_config(config)
