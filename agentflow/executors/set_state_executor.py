"""
SetState Executor — writes literal values or state references into graph_state.

Implements A3 from the implementation plan.
SPEC references:
- §3.1: Controlled writes (only declared fields)
- §3.4: SET_STATE node contract
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import ExecutionState, NodeDefinition, SetStateNodeConfig


class SetStateExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        """
        Resolve assignments and return state updates.

        Values that start with "state." are resolved as references to the
        current graph_state. All other values are treated as literals.
        """
        config = SetStateNodeConfig(**node_def.config)
        state_updates: dict[str, Any] = {}

        for field_name, raw_value in config.assignments.items():
            if isinstance(raw_value, str) and raw_value.startswith("state."):
                ref_key = raw_value[len("state."):]
                resolved = execution_state.graph_state.get(ref_key)
                state_updates[field_name] = resolved
            else:
                state_updates[field_name] = raw_value

        return state_updates
