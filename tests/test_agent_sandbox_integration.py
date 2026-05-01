from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class _FakeConnectionConfig:
    mode: str
    values: dict[str, object]


class _FakeSandboxClient:
    instances: list["_FakeSandboxClient"] = []

    def __init__(self, connection_config: _FakeConnectionConfig) -> None:
        self.connection_config = connection_config
        self.create_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        _FakeSandboxClient.instances.append(self)

    def create_sandbox(
        self,
        *,
        template_name: str,
        namespace: str,
        ready_timeout: int,
        shutdown_after_seconds: int | None = None,
    ) -> object:
        call = {
            "template_name": template_name,
            "namespace": namespace,
            "ready_timeout": ready_timeout,
            "shutdown_after_seconds": shutdown_after_seconds,
        }
        self.create_calls.append(call)
        return types.SimpleNamespace(
            claim_name="claim-created",
            namespace=namespace,
            sandbox_id=f"{namespace}/claim-created",
        )

    def get_sandbox(self, *, claim_name: str, namespace: str) -> object:
        call = {"claim_name": claim_name, "namespace": namespace}
        self.get_calls.append(call)
        return types.SimpleNamespace(
            claim_name=claim_name,
            namespace=namespace,
            sandbox_id=f"{namespace}/{claim_name}",
        )


class _FakeAgentSandboxBackend:
    def __init__(self, sandbox: object, root_dir: str | None = None) -> None:
        self.sandbox = sandbox
        self.root_dir = root_dir
        self.id = getattr(sandbox, "sandbox_id", "unknown/unknown")

    @classmethod
    def from_existing(cls, sandbox: object, *, root_dir: str | None = None) -> "_FakeAgentSandboxBackend":
        return cls(sandbox, root_dir=root_dir)


def _install_fake_agent_sandbox_sdks(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSandboxClient.instances.clear()

    k8s_module = types.ModuleType("k8s_agent_sandbox")
    k8s_module.SandboxClient = _FakeSandboxClient
    k8s_module.TunnelConnectionConfig = lambda: _FakeConnectionConfig("tunnel", {})
    k8s_module.GatewayConnectionConfig = lambda *, gateway_name, gateway_namespace: _FakeConnectionConfig(
        "gateway",
        {
            "gateway_name": gateway_name,
            "gateway_namespace": gateway_namespace,
        },
    )
    k8s_module.DirectConnectionConfig = lambda *, api_url: _FakeConnectionConfig(
        "direct",
        {"api_url": api_url},
    )

    langchain_module = types.ModuleType("langchain_agent_sandbox")
    langchain_module.AgentSandboxBackend = _FakeAgentSandboxBackend

    monkeypatch.setitem(sys.modules, "k8s_agent_sandbox", k8s_module)
    monkeypatch.setitem(sys.modules, "langchain_agent_sandbox", langchain_module)


def _install_existing_provider_import_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    deepagents_backends = types.ModuleType("deepagents.backends")
    deepagents_backends.LangSmithSandbox = object
    deepagents_backends.LocalShellBackend = object
    monkeypatch.setitem(sys.modules, "deepagents.backends", deepagents_backends)

    deepagents_protocol = types.ModuleType("deepagents.backends.protocol")
    deepagents_protocol.SandboxBackendProtocol = object
    monkeypatch.setitem(sys.modules, "deepagents.backends.protocol", deepagents_protocol)

    langsmith_sandbox = types.ModuleType("langsmith.sandbox")
    langsmith_sandbox.SandboxClient = object
    langsmith_sandbox.SandboxTemplate = object
    langsmith_sandbox.SandboxClientError = RuntimeError
    monkeypatch.setitem(sys.modules, "langsmith.sandbox", langsmith_sandbox)

    daytona_module = types.ModuleType("daytona")
    daytona_module.CreateSandboxFromSnapshotParams = lambda **kwargs: kwargs
    daytona_module.Daytona = object
    daytona_module.DaytonaConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "daytona", daytona_module)

    langchain_daytona = types.ModuleType("langchain_daytona")
    langchain_daytona.DaytonaSandbox = object
    monkeypatch.setitem(sys.modules, "langchain_daytona", langchain_daytona)

    modal_module = types.ModuleType("modal")
    modal_module.App = types.SimpleNamespace(lookup=lambda name: types.SimpleNamespace(name=name))
    modal_module.Sandbox = types.SimpleNamespace(
        from_id=lambda sandbox_id, app=None: types.SimpleNamespace(id=sandbox_id, app=app),
        create=lambda app=None: types.SimpleNamespace(id="modal-created", app=app),
    )
    monkeypatch.setitem(sys.modules, "modal", modal_module)

    langchain_modal = types.ModuleType("langchain_modal")
    langchain_modal.ModalSandbox = object
    monkeypatch.setitem(sys.modules, "langchain_modal", langchain_modal)

    langchain_runloop = types.ModuleType("langchain_runloop")
    langchain_runloop.RunloopSandbox = object
    monkeypatch.setitem(sys.modules, "langchain_runloop", langchain_runloop)

    runloop_api_client = types.ModuleType("runloop_api_client")
    runloop_api_client.Client = object
    monkeypatch.setitem(sys.modules, "runloop_api_client", runloop_api_client)


def _load_sandbox_utils(monkeypatch: pytest.MonkeyPatch):
    _install_existing_provider_import_stubs(monkeypatch)
    sys.modules.pop("agent.utils.sandbox", None)
    return importlib.import_module("agent.utils.sandbox")


def _reload_agent_sandbox_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _install_existing_provider_import_stubs(monkeypatch)
    sys.modules.pop("agent.integrations.agent_sandbox", None)
    return importlib.import_module("agent.integrations.agent_sandbox")


def test_provider_registration_accepts_agent_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox_utils = _load_sandbox_utils(monkeypatch)
    assert "agent_sandbox" in sandbox_utils.SANDBOX_FACTORIES

    sentinel = object()
    monkeypatch.setitem(sandbox_utils.SANDBOX_FACTORIES, "agent_sandbox", lambda sandbox_id=None: sentinel)
    monkeypatch.setenv("SANDBOX_TYPE", "agent_sandbox")

    assert sandbox_utils.create_sandbox() is sentinel


def test_invalid_sandbox_type_lists_agent_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox_utils = _load_sandbox_utils(monkeypatch)
    monkeypatch.setenv("SANDBOX_TYPE", "does-not-exist")

    with pytest.raises(ValueError, match=r"agent_sandbox"):
        sandbox_utils.create_sandbox()


def test_create_agent_sandbox_uses_env_driven_client_and_preserves_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_TEMPLATE_NAME", "open-swe-template")
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "swe")
    monkeypatch.setenv("AGENT_SANDBOX_READY_TIMEOUT", "42")
    monkeypatch.setenv("AGENT_SANDBOX_SHUTDOWN_AFTER_SECONDS", "900")
    monkeypatch.setenv("AGENT_SANDBOX_CONNECTION_MODE", "tunnel")
    module = _reload_agent_sandbox_module(monkeypatch)

    backend = module.create_agent_sandbox()

    client = _FakeSandboxClient.instances[-1]
    assert client.connection_config.mode == "tunnel"
    assert client.create_calls == [
        {
            "template_name": "open-swe-template",
            "namespace": "swe",
            "ready_timeout": 42,
            "shutdown_after_seconds": 900,
        }
    ]
    assert backend.root_dir == "/app"
    assert getattr(backend, "default_timeout_seconds", None) == 300
    assert backend.id == "swe/claim-created"


def test_create_agent_sandbox_honors_overridden_root_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_TEMPLATE_NAME", "open-swe-template")
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "swe")
    monkeypatch.setenv("AGENT_SANDBOX_ROOT_DIR", "/workspace")
    module = _reload_agent_sandbox_module(monkeypatch)

    backend = module.create_agent_sandbox()

    assert backend.root_dir == "/workspace"


def test_reconnect_parses_namespace_qualified_sandbox_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "fallback")
    module = _reload_agent_sandbox_module(monkeypatch)

    backend = module.create_agent_sandbox("team-a/claim-123")

    client = _FakeSandboxClient.instances[-1]
    assert client.get_calls == [{"claim_name": "claim-123", "namespace": "team-a"}]
    assert backend.id == "team-a/claim-123"


def test_reconnect_accepts_bare_claim_name_and_uses_configured_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "fallback")
    module = _reload_agent_sandbox_module(monkeypatch)

    backend = module.create_agent_sandbox("claim-123")

    client = _FakeSandboxClient.instances[-1]
    assert client.get_calls == [{"claim_name": "claim-123", "namespace": "fallback"}]
    assert backend.id == "fallback/claim-123"


@pytest.mark.parametrize("sandbox_id", ["namespace/claim/extra", "/claim", "namespace/"])
def test_invalid_sandbox_id_format_raises_informative_value_error(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_id: str,
) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "fallback")
    module = _reload_agent_sandbox_module(monkeypatch)

    with pytest.raises(ValueError, match=r"namespace/claim_name|sandbox id"):
        module.create_agent_sandbox(sandbox_id)


@pytest.mark.parametrize(
    ("mode", "extra_env", "expected_mode", "expected_values"),
    [
        ("tunnel", {}, "tunnel", {}),
        (
            "gateway",
            {
                "AGENT_SANDBOX_GATEWAY_NAME": "sandbox-gateway",
                "AGENT_SANDBOX_GATEWAY_NAMESPACE": "sandbox-system",
            },
            "gateway",
            {
                "gateway_name": "sandbox-gateway",
                "gateway_namespace": "sandbox-system",
            },
        ),
        (
            "direct",
            {"AGENT_SANDBOX_API_URL": "https://agent-sandbox.internal"},
            "direct",
            {"api_url": "https://agent-sandbox.internal"},
        ),
    ],
)
def test_connection_mode_mapping(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    extra_env: dict[str, str],
    expected_mode: str,
    expected_values: dict[str, object],
) -> None:
    _install_fake_agent_sandbox_sdks(monkeypatch)
    monkeypatch.setenv("AGENT_SANDBOX_CONNECTION_MODE", mode)
    monkeypatch.setenv("AGENT_SANDBOX_TEMPLATE_NAME", "open-swe-template")
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "swe")
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    module = _reload_agent_sandbox_module(monkeypatch)

    module.create_agent_sandbox()

    client = _FakeSandboxClient.instances[-1]
    assert client.connection_config.mode == expected_mode
    assert client.connection_config.values == expected_values


def test_import_is_lazy_when_sdk_packages_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "k8s_agent_sandbox", raising=False)
    monkeypatch.delitem(sys.modules, "langchain_agent_sandbox", raising=False)

    module = _reload_agent_sandbox_module(monkeypatch)

    assert module is not None


def test_factory_raises_clear_runtime_error_when_sdk_packages_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "k8s_agent_sandbox", raising=False)
    monkeypatch.delitem(sys.modules, "langchain_agent_sandbox", raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_TEMPLATE_NAME", "open-swe-template")
    monkeypatch.setenv("AGENT_SANDBOX_NAMESPACE", "swe")
    module = _reload_agent_sandbox_module(monkeypatch)

    with pytest.raises(RuntimeError, match=r"k8s_agent_sandbox|langchain_agent_sandbox"):
        module.create_agent_sandbox()
