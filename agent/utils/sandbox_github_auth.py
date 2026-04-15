from __future__ import annotations

import os
import shlex

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.integrations.langsmith import _configure_github_proxy


def _result_output(result: object) -> str:
    parts: list[str] = []
    for attr in ("output", "stdout", "stderr"):
        value = getattr(result, attr, None)
        if value:
            parts.append(str(value).strip())
    return "\n".join(part for part in parts if part)


def _run_or_raise(
    sandbox_backend: SandboxBackendProtocol,
    command: str,
    *,
    description: str,
) -> None:
    result = sandbox_backend.execute(command)
    if getattr(result, "exit_code", 0) == 0:
        return

    output = _result_output(result)
    msg = f"{description} failed"
    if output:
        msg = f"{msg}: {output}"
    raise RuntimeError(msg)


def _configure_agent_sandbox_git_credentials(
    sandbox_backend: SandboxBackendProtocol,
    github_token: str,
) -> None:
    credential = f"https://x-access-token:{github_token}@github.com"
    quoted_credential = shlex.quote(f"{credential}\n")

    _run_or_raise(
        sandbox_backend,
        "git config --global credential.helper store",
        description="configuring git credential storage",
    )
    _run_or_raise(
        sandbox_backend,
        f"printf %s {quoted_credential} > \"$HOME/.git-credentials\"",
        description="writing sandbox git credentials",
    )


def configure_github_network_access(
    sandbox_backend: SandboxBackendProtocol,
    github_token: str,
) -> None:
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")

    if sandbox_type == "langsmith":
        _configure_github_proxy(sandbox_backend.id, github_token)
        return

    if sandbox_type == "agent_sandbox":
        _configure_agent_sandbox_git_credentials(sandbox_backend, github_token)
        return
