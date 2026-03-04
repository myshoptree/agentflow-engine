"""
Parallel Node Executor (Phase 2 — stub for Phase 1).

Executes multiple branch nodes concurrently via asyncio.gather.
Each branch is an independent node_id in the graph. Results are merged
into state_updates using the parallel node's output_mapping.

Note: parallel branches run in the same execution context; isolation
is per-execution, not per-branch (SPEC §1.2 applies at execution level).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentflow.core.models import ExecutionState, NodeDefinition, ParallelNodeConfig

if TYPE_CHECKING:
    from agentflow.core.compiler import CompiledGraph


class ParallelExecutorError(Exception):
    pass


class ParallelExecutor:
    def __init__(self, compiled_graph: "CompiledGraph") -> None:
        self._graph = compiled_graph

    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        config = ParallelNodeConfig(**node_def.config)

        # Import here to avoid circular imports
        from agentflow.executors.registry import get_executor

        async def run_branch(branch_node_id: str) -> dict[str, Any]:
            branch_node = self._graph.nodes.get(branch_node_id)
            if branch_node is None:
                raise ParallelExecutorError(
                    f"Parallel branch node '{branch_node_id}' not found in graph"
                )
            executor = get_executor(branch_node.definition.type, self._graph)
            return await executor.execute(
                branch_node_id,
                branch_node.definition,
                execution_state,
            )

        results = await asyncio.gather(
            *[run_branch(b) for b in config.branches],
            return_exceptions=True,
        )

        # Check for branch failures
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            raise ParallelExecutorError(
                f"Parallel branches failed: {[str(e) for e in errors]}"
            )

        # Merge all branch outputs — later branches override earlier ones on key conflict
        merged: dict[str, Any] = {}
        for branch_result in results:
            if isinstance(branch_result, dict):
                merged.update(branch_result)

        return merged
