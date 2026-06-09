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


# In-process credential cache (built into git): a small daemon that holds the
# password in process memory and serves it over a unix socket on demand. The
# timeout is generous enough to outlast a typical agent run; the daemon
# clears the credential when the timeout expires or when the sandbox is
# destroyed. The GitHub App installation token is short-lived
# (typically ~1h) so the effective lifetime is bounded by whichever expires
# first.
_GIT_CREDENTIAL_CACHE_TIMEOUT_SECONDS = 3600

# Wrapper installed in PATH that re-pulls the token from git's credential
# cache and runs ``gh`` with it in env. This keeps ``~/.config/gh/hosts.yml``
# off disk: gh would otherwise persist the token there on ``gh auth login``.
_GH_WRAPPER_SCRIPT = """\
#!/bin/sh
# openswe gh wrapper — fetches the GitHub token from git's in-memory
# credential cache and runs gh with it in env. The token is never written
# to disk by this wrapper. Use just like `gh` (e.g. `gh-secure pr create`).
set -eu
token=$(printf 'protocol=https\\nhost=github.com\\n\\n' \\
    | git credential fill \\
    | sed -n 's/^password=//p')
if [ -z "${token}" ]; then
    echo "openswe gh-secure: no GitHub credentials cached" >&2
    exit 2
fi
GH_TOKEN="${token}" gh "$@"
"""


def _shell_quote_heredoc_payload(value: str) -> str:
    """Quote ``value`` so it can be safely embedded as a here-doc body."""
    return value.replace("\\", "\\\\").replace("$", "\\$").replace("`", "\\`")


def _configure_agent_sandbox_git_credentials(
    sandbox_backend: SandboxBackendProtocol,
    github_token: str,
) -> None:
    """Configure git/gh auth without ever writing the token to disk.

    The token is loaded into ``git credential-cache--daemon`` — git's
    built-in in-memory credential daemon — and accessed via a unix socket
    on demand. Compared to writing ``~/.git-credentials`` directly, this:

    - keeps the plain-text token out of any file on disk inside the sandbox;
    - bounds exposure to the credential-cache daemon's process memory;
    - expires automatically after ``_GIT_CREDENTIAL_CACHE_TIMEOUT_SECONDS``.

    Limitations (compared to the LangSmith external proxy):

    - The token is briefly present in the argv of the ``git credential
      approve`` invocation (visible via ``ps`` during that one command).
    - Any process running as the sandbox user can still read the token via
      the credential cache socket. True host-side isolation requires an
      external egress proxy — that's what the LangSmith provider does, and
      is outside the agent_sandbox provider's current capabilities.

    For ``gh``: a wrapper script installed at ``/usr/local/bin/gh-secure``
    fetches the token from the cache and runs ``gh`` with ``GH_TOKEN`` set.
    Use ``gh-secure`` instead of ``gh`` to avoid ``gh auth login`` writing
    the token to ``~/.config/gh/hosts.yml``.
    """
    _run_or_raise(
        sandbox_backend,
        (
            "git config --global credential.helper "
            f"'cache --timeout={_GIT_CREDENTIAL_CACHE_TIMEOUT_SECONDS}'"
        ),
        description="configuring git credential cache",
    )

    payload = f"url=https://github.com\nusername=x-access-token\npassword={github_token}\n\n"
    quoted_payload = shlex.quote(payload)
    # ``printf %s`` is a shell builtin in dash/bash, so the token does not
    # appear as the argv of a separate process; it is present only in the
    # parent shell's argv for the duration of this one command.
    _run_or_raise(
        sandbox_backend,
        f"printf %s {quoted_payload} | git credential approve",
        description="loading git credentials into cache",
    )

    # Reject any previously-stored credential file in case an earlier run on
    # this sandbox wrote one — we don't want the token sitting in
    # ~/.git-credentials after upgrading the auth scheme.
    _run_or_raise(
        sandbox_backend,
        'rm -f "$HOME/.git-credentials" "$HOME/.config/gh/hosts.yml"',
        description="clearing pre-existing on-disk credentials",
    )

    # Install the gh-secure wrapper. Heredoc keeps the script content out of
    # argv (only the path appears in ``ps``).
    quoted_script = _shell_quote_heredoc_payload(_GH_WRAPPER_SCRIPT)
    install_script_cmd = (
        "cat <<'OPENSWE_GH_WRAPPER_EOF' >/usr/local/bin/gh-secure\n"
        f"{quoted_script}"
        "OPENSWE_GH_WRAPPER_EOF\n"
        "chmod 0755 /usr/local/bin/gh-secure"
    )
    _run_or_raise(
        sandbox_backend,
        f"sh -c {shlex.quote(install_script_cmd)}",
        description="installing gh-secure wrapper",
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
