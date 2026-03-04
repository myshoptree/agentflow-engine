"""
Tool Node Executor.

Executes a registered tool function by name. Tools are plain async callables
registered in the ToolRegistry. Input is mapped from graph_state, output is
returned as a dict for the runtime to apply state_output_mapping.

SPEC §5.1: transient errors are retried by the runtime (not here).
"""
from __future__ import annotations

from typing import Any, Callable, Awaitable

from agentflow.core.models import ExecutionState, NodeDefinition, ToolNodeConfig


class ToolExecutorError(Exception):
    pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Signature: async def tool_fn(**kwargs) -> dict[str, Any]
ToolFn = Callable[..., Awaitable[dict[str, Any]]]

_registry: dict[str, ToolFn] = {}


def register_tool(name: str, fn: ToolFn) -> None:
    _registry[name] = fn


def get_tool(name: str) -> ToolFn:
    if name not in _registry:
        raise ToolExecutorError(f"Tool '{name}' is not registered")
    return _registry[name]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class ToolExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        config = ToolNodeConfig(**node_def.config)
        tool_fn = get_tool(config.tool_name)

        # Build tool input from graph_state using input_mapping
        # input_mapping: {tool_param: state_field}
        kwargs: dict[str, Any] = {}
        for tool_param, state_field in config.input_mapping.items():
            if state_field not in execution_state.graph_state:
                raise ToolExecutorError(
                    f"Tool '{config.tool_name}' requires input '{tool_param}' "
                    f"mapped from state field '{state_field}' which does not exist"
                )
            kwargs[tool_param] = execution_state.graph_state[state_field]

        result = await tool_fn(**kwargs)

        if not isinstance(result, dict):
            raise ToolExecutorError(
                f"Tool '{config.tool_name}' must return a dict, got {type(result).__name__}"
            )

        # Apply output_mapping: {state_field: tool_output_field}
        state_updates: dict[str, Any] = {}
        for state_field, output_field in config.output_mapping.items():
            if output_field not in result:
                raise ToolExecutorError(
                    f"Tool '{config.tool_name}' output missing field '{output_field}'"
                )
            state_updates[state_field] = result[output_field]

        return state_updates
