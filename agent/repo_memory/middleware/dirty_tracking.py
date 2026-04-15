from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

try:
    from langchain.agents.middleware.types import AgentMiddleware, AgentState
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs
    class AgentState(dict):
        pass

    class AgentMiddleware:
        pass

try:
    from langchain_core.messages import ToolMessage
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs
    ToolMessage = Any

try:
    from langgraph.prebuilt.tool_node import ToolCallRequest
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs
    ToolCallRequest = Any

try:
    from langgraph.types import Command
except ModuleNotFoundError:  # pragma: no cover - exercised in stripped test envs
    Command = Any

from ..delta import mark_execute_dirty_unknown
from ..focus import compute_focus_set
from ..runtime import resolve_runtime_from_context, runtime_attr


def update_state_for_tool(
    state: dict,
    *,
    tool_name: str,
    tool_args: dict | None = None,
    tool_result: object | None = None,
) -> dict:
    tool_args = tool_args or {}
    runtime = resolve_runtime_from_context(state)
    if runtime is not None:
        state["repo_memory_runtime"] = runtime
    config = runtime_attr(runtime, "config", None)
    dirty_paths = set(state.get("dirty_paths", set()))
    focus_paths = list(state.get("focus_paths", []))
    focus_entities = list(state.get("focus_entities", []))

    if tool_name in {"write_file", "edit_file"}:
        path = tool_args.get("path")
        if path:
            dirty_paths.add(path)
            focus_paths.append(path)

    if tool_name == "read_file":
        path = tool_args.get("path")
        if path:
            focus_paths.append(path)

    if tool_name == "grep":
        pattern = tool_args.get("pattern")
        if pattern:
            focus_entities.append(pattern)
        if isinstance(tool_result, dict):
            for match in tool_result.get("matches", []):
                path = match.get("path")
                if path:
                    focus_paths.append(path)

    if tool_name == "execute":
        exit_code = getattr(tool_result, "exit_code", None)
        if isinstance(tool_result, dict):
            exit_code = tool_result.get("exit_code", exit_code)
        if isinstance(exit_code, int):
            mark_execute_dirty_unknown(
                state,
                exit_code,
                dirty_exit_codes=runtime_attr(config, "dirty_execute_exit_codes", None),
            )

    state["dirty_paths"] = dirty_paths
    focus = compute_focus_set(explicit_paths=focus_paths, explicit_entities=focus_entities)
    state["focus_paths"] = focus.paths
    state["focus_entities"] = focus.entities
    return state


class RepoMemoryToolMiddleware(AgentMiddleware):
    state_schema = AgentState

    def _request_state(self, request: ToolCallRequest) -> dict | None:
        for attr in ("state", "agent_state"):
            state = getattr(request, attr, None)
            if isinstance(state, dict):
                return state
        return None

    def _request_name_args(self, request: ToolCallRequest) -> tuple[str | None, dict]:
        tool_call = getattr(request, "tool_call", None)
        if isinstance(tool_call, dict):
            return tool_call.get("name"), tool_call.get("args", {}) or {}
        return getattr(request, "tool_name", None), {}

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        state = self._request_state(request)
        name, args = self._request_name_args(request)
        if state is not None and name:
            update_state_for_tool(state, tool_name=name, tool_args=args, tool_result=result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        state = self._request_state(request)
        name, args = self._request_name_args(request)
        if state is not None and name:
            update_state_for_tool(state, tool_name=name, tool_args=args, tool_result=result)
        return result
