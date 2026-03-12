"""
Executor registry — maps NodeType to the correct executor instance.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentflow.core.models import ExecutionState, NodeDefinition, NodeType

if TYPE_CHECKING:
    from agentflow.core.compiler import CompiledGraph


class UnknownNodeTypeError(Exception):
    pass


def get_executor(node_type: NodeType, compiled_graph: "CompiledGraph") -> Any:
    from agentflow.executors.agent_executor import AgentExecutor
    from agentflow.executors.condition_executor import ConditionExecutor
    from agentflow.executors.guardrail_executor import GuardrailExecutor
    from agentflow.executors.parallel_executor import ParallelExecutor
    from agentflow.executors.set_state_executor import SetStateExecutor
    from agentflow.executors.tool_executor import ToolExecutor
    from agentflow.executors.transform_executor import TransformExecutor

    if node_type == NodeType.AGENT:
        return AgentExecutor()
    if node_type == NodeType.CONDITION:
        return ConditionExecutor()
    if node_type == NodeType.TOOL:
        return ToolExecutor()
    if node_type == NodeType.PARALLEL:
        return ParallelExecutor(compiled_graph)
    if node_type == NodeType.END:
        # END nodes are handled directly in the runtime loop
        return _EndExecutor()
    if node_type == NodeType.HUMAN_INPUT:
        return _HumanInputExecutor()
    if node_type == NodeType.SET_STATE:
        return SetStateExecutor()
    if node_type == NodeType.TRANSFORM:
        return TransformExecutor()
    if node_type == NodeType.GUARDRAIL:
        return GuardrailExecutor()
    # START is handled directly in the runtime loop (no executor)
    if node_type == NodeType.START:
        return _NoOpExecutor()

    raise UnknownNodeTypeError(f"No executor registered for node type '{node_type}'")


class _EndExecutor:
    """Sentinel — runtime detects END nodes before dispatching."""
    async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}


class _NoOpExecutor:
    """Sentinel for nodes handled entirely by the runtime loop (START)."""
    async def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}


class _HumanInputExecutor:
    """Signals the runtime to suspend the execution (SPEC §6.1)."""
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        # Runtime checks for HUMAN_INPUT type before calling execute
        # and handles suspension. This should not be reached normally.
        raise RuntimeError(
            "HumanInputExecutor.execute() called directly — "
            "the runtime should handle suspension before dispatching."
        )
