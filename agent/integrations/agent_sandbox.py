"""Agent Sandbox backend integration."""

from __future__ import annotations

import os


DEFAULT_ROOT_DIR = "/app"
DEFAULT_EXECUTE_TIMEOUT_SECONDS = 300


def _load_agent_sandbox_sdks():
    try:
        from k8s_agent_sandbox import (
            DirectConnectionConfig,
            GatewayConnectionConfig,
            SandboxClient,
            TunnelConnectionConfig,
        )
        from langchain_agent_sandbox import AgentSandboxBackend
    except ImportError as exc:
        msg = (
            "agent_sandbox provider requires both k8s_agent_sandbox and "
            "langchain_agent_sandbox to be installed"
        )
        raise RuntimeError(msg) from exc

    return (
        SandboxClient,
        TunnelConnectionConfig,
        GatewayConnectionConfig,
        DirectConnectionConfig,
        AgentSandboxBackend,
    )


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} must be set for SANDBOX_TYPE=agent_sandbox")
    return value


def _build_connection_config():
    (
        _sandbox_client,
        tunnel_connection_config,
        gateway_connection_config,
        direct_connection_config,
        _agent_sandbox_backend,
    ) = _load_agent_sandbox_sdks()

    mode = os.getenv("AGENT_SANDBOX_CONNECTION_MODE", "tunnel").strip().lower()
    if mode == "tunnel":
        return tunnel_connection_config()
    if mode == "gateway":
        return gateway_connection_config(
            gateway_name=_get_required_env("AGENT_SANDBOX_GATEWAY_NAME"),
            gateway_namespace=_get_required_env("AGENT_SANDBOX_GATEWAY_NAMESPACE"),
        )
    if mode == "direct":
        return direct_connection_config(api_url=_get_required_env("AGENT_SANDBOX_API_URL"))

    raise ValueError(
        "AGENT_SANDBOX_CONNECTION_MODE must be one of: tunnel, gateway, direct"
    )


def _parse_int_env(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def _parse_sandbox_id(sandbox_id: str, default_namespace: str) -> tuple[str, str]:
    parts = sandbox_id.split("/")
    if len(parts) == 1:
        claim_name = parts[0]
        if not claim_name:
            raise ValueError("agent_sandbox sandbox id must be namespace/claim_name or claim_name")
        return default_namespace, claim_name
    if len(parts) == 2 and all(parts):
        namespace, claim_name = parts
        return namespace, claim_name
    raise ValueError("agent_sandbox sandbox id must be namespace/claim_name or claim_name")


def create_agent_sandbox(sandbox_id: str | None = None):
    (
        sandbox_client,
        _tunnel_connection_config,
        _gateway_connection_config,
        _direct_connection_config,
        agent_sandbox_backend,
    ) = _load_agent_sandbox_sdks()

    namespace = _get_required_env("AGENT_SANDBOX_NAMESPACE")
    root_dir = os.getenv("AGENT_SANDBOX_ROOT_DIR", DEFAULT_ROOT_DIR)
    ready_timeout = _parse_int_env("AGENT_SANDBOX_READY_TIMEOUT", 180)
    shutdown_after_seconds = _parse_int_env("AGENT_SANDBOX_SHUTDOWN_AFTER_SECONDS")
    default_timeout_seconds = _parse_int_env(
        "AGENT_SANDBOX_DEFAULT_TIMEOUT_SECONDS",
        DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    )

    client = sandbox_client(connection_config=_build_connection_config())

    if sandbox_id:
        sandbox_namespace, claim_name = _parse_sandbox_id(sandbox_id, namespace)
        sandbox = client.get_sandbox(claim_name=claim_name, namespace=sandbox_namespace)
    else:
        sandbox = client.create_sandbox(
            template_name=_get_required_env("AGENT_SANDBOX_TEMPLATE_NAME"),
            namespace=namespace,
            ready_timeout=ready_timeout,
            shutdown_after_seconds=shutdown_after_seconds,
        )

    backend = agent_sandbox_backend.from_existing(sandbox, root_dir=root_dir)
    setattr(backend, "default_timeout_seconds", default_timeout_seconds)
    return backend
