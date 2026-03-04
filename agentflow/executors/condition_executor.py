"""
Condition Node Executor.

Does not invoke any LLM. Transition resolution happens in the runtime
by evaluating edge conditions. This executor is a no-op — it just
signals the runtime to proceed to transition resolution.
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import ConditionNodeConfig, ExecutionState, NodeDefinition


class ConditionExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        """Returns empty dict — state is not modified by a condition node."""
        return {}
