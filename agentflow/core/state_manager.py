"""
State Manager — handles execution state lifecycle and checkpointing.

Implements the abstract StateManagerProtocol. The InMemoryStateManager
is used for Phase 1 (MVP). A PostgreSQL implementation can be swapped in
for Phase 2 without changing any executor or runtime code.

SPEC references:
- §1.2: Execution isolation (no shared mutable state between executions)
- §1.3: Every execution must reach a terminal status
- §3.2: Checkpoints are immutable snapshots
- §6.2: Only SUSPENDED executions can be resumed
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agentflow.core.models import (
    ExecutionState,
    ExecutionStatus,
    ExecutionTrace,
    GradeResult,
    GraphDefinition,
    NodeExecutionRecord,
    StateCheckpoint,
)


class StateManagerError(Exception):
    pass


class StateManagerProtocol(ABC):
    @abstractmethod
    async def create_execution(
        self,
        graph: GraphDefinition,
        initial_input: dict[str, Any],
        execution_id: str | None = None,
    ) -> ExecutionState:
        ...

    @abstractmethod
    async def load_execution(self, execution_id: str) -> ExecutionState:
        ...

    @abstractmethod
    async def save_execution(self, state: ExecutionState) -> None:
        ...

    @abstractmethod
    async def update_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        error_message: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    async def checkpoint(self, execution_state: ExecutionState, node_id: str) -> StateCheckpoint:
        ...

    @abstractmethod
    async def get_checkpoints(self, execution_id: str) -> list[StateCheckpoint]:
        ...

    @abstractmethod
    async def record_node_execution(self, record: NodeExecutionRecord) -> None:
        ...

    @abstractmethod
    async def resume(self, execution_id: str, input_data: dict[str, Any]) -> ExecutionState:
        ...

    @abstractmethod
    async def cancel(self, execution_id: str) -> None:
        ...

    @abstractmethod
    async def get_node_records(self, execution_id: str) -> list[NodeExecutionRecord]:
        ...

    @abstractmethod
    async def get_trace(self, execution_id: str) -> ExecutionTrace:
        ...

    @abstractmethod
    async def save_grade(self, grade: GradeResult) -> None:
        ...

    @abstractmethod
    async def get_grades(self, execution_id: str) -> list[GradeResult]:
        ...


# ---------------------------------------------------------------------------
# In-Memory implementation (Phase 1)
# ---------------------------------------------------------------------------

class InMemoryStateManager(StateManagerProtocol):
    """
    Thread-unsafe in-memory state manager for single-process use.
    Each execution state is stored as a deep copy to enforce isolation (SPEC §1.2).
    """

    def __init__(self) -> None:
        self._executions: dict[str, ExecutionState] = {}
        self._checkpoints: dict[str, list[StateCheckpoint]] = {}
        self._node_records: dict[str, list[NodeExecutionRecord]] = {}
        self._grades: dict[str, list[GradeResult]] = {}
        self._execution_start_times: dict[str, float] = {}  # for duration_ms

    async def create_execution(
        self,
        graph: GraphDefinition,
        initial_input: dict[str, Any],
        execution_id: str | None = None,
    ) -> ExecutionState:
        # Build initial state from schema defaults + initial_input (SPEC §3.3)
        graph_state: dict[str, Any] = {}
        for field in graph.state_schema:
            if field.default is not None:
                graph_state[field.name] = copy.deepcopy(field.default)

        graph_state.update(initial_input)

        state = ExecutionState(
            graph_id=graph.id,
            graph_version=graph.version,
            status=ExecutionStatus.PENDING,
            current_node=graph.entry_node,
            graph_state=graph_state,
        )

        if execution_id is not None:
            # SPEC §9.3: explicit ID must not already exist
            if execution_id in self._executions:
                raise StateManagerError(
                    f"Execution '{execution_id}' already exists"
                )
            state.execution_id = execution_id

        # Store a deep copy to isolate from caller mutations
        self._executions[state.execution_id] = copy.deepcopy(state)
        self._checkpoints[state.execution_id] = []
        self._node_records[state.execution_id] = []
        self._grades[state.execution_id] = []

        import time
        self._execution_start_times[state.execution_id] = time.monotonic()

        return copy.deepcopy(state)

    async def load_execution(self, execution_id: str) -> ExecutionState:
        state = self._executions.get(execution_id)
        if state is None:
            raise StateManagerError(f"Execution '{execution_id}' not found")
        return copy.deepcopy(state)

    async def save_execution(self, state: ExecutionState) -> None:
        if state.execution_id not in self._executions:
            raise StateManagerError(f"Execution '{state.execution_id}' not found")
        state.updated_at = datetime.utcnow()
        self._executions[state.execution_id] = copy.deepcopy(state)

    async def update_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        error_message: str | None = None,
    ) -> None:
        state = self._executions.get(execution_id)
        if state is None:
            raise StateManagerError(f"Execution '{execution_id}' not found")
        state.status = status
        state.updated_at = datetime.utcnow()
        if error_message is not None:
            state.error_message = error_message

    async def checkpoint(
        self, execution_state: ExecutionState, node_id: str
    ) -> StateCheckpoint:
        """Create an immutable snapshot of the current graph_state (SPEC §3.2)."""
        snapshot = StateCheckpoint(
            execution_id=execution_state.execution_id,
            node_id=node_id,
            graph_state_snapshot=copy.deepcopy(execution_state.graph_state),
            depth=execution_state.depth,
        )
        self._checkpoints[execution_state.execution_id].append(snapshot)
        return snapshot

    async def get_checkpoints(self, execution_id: str) -> list[StateCheckpoint]:
        return list(self._checkpoints.get(execution_id, []))

    async def record_node_execution(self, record: NodeExecutionRecord) -> None:
        records = self._node_records.setdefault(record.execution_id, [])
        records.append(record)

    async def resume(
        self, execution_id: str, input_data: dict[str, Any]
    ) -> ExecutionState:
        """Resume a SUSPENDED execution (SPEC §6.2)."""
        state = self._executions.get(execution_id)
        if state is None:
            raise StateManagerError(f"Execution '{execution_id}' not found")
        if state.status != ExecutionStatus.SUSPENDED:
            raise StateManagerError(
                f"Cannot resume execution '{execution_id}' — "
                f"current status is '{state.status}' (must be SUSPENDED)"
            )
        state.graph_state.update(input_data)
        state.status = ExecutionStatus.RUNNING
        state.updated_at = datetime.utcnow()
        state.suspended_input_key = None
        return copy.deepcopy(state)

    async def cancel(self, execution_id: str) -> None:
        state = self._executions.get(execution_id)
        if state is None:
            raise StateManagerError(f"Execution '{execution_id}' not found")
        if state.status not in (ExecutionStatus.RUNNING, ExecutionStatus.SUSPENDED):
            raise StateManagerError(
                f"Cannot cancel execution '{execution_id}' — "
                f"current status is '{state.status}'"
            )
        state.status = ExecutionStatus.CANCELLED
        state.updated_at = datetime.utcnow()

    async def get_node_records(self, execution_id: str) -> list[NodeExecutionRecord]:
        return list(self._node_records.get(execution_id, []))

    async def get_trace(self, execution_id: str) -> ExecutionTrace:
        """Build an ExecutionTrace from stored records (SPEC §7.4)."""
        import time

        state = self._executions.get(execution_id)
        if state is None:
            raise StateManagerError(f"Execution '{execution_id}' not found")

        records = list(self._node_records.get(execution_id, []))
        checkpoints = list(self._checkpoints.get(execution_id, []))

        start_time = self._execution_start_times.get(execution_id)
        if start_time is not None:
            duration_ms = (time.monotonic() - start_time) * 1000
        else:
            duration_ms = 0.0

        total_tokens = sum(r.llm_tokens_used for r in records)

        return ExecutionTrace(
            execution_id=execution_id,
            graph_id=state.graph_id,
            graph_version=state.graph_version,
            status=state.status,
            duration_ms=duration_ms,
            total_tokens=total_tokens,
            node_records=records,
            checkpoints=checkpoints,
            final_state=copy.deepcopy(state.graph_state),
        )

    async def save_grade(self, grade: GradeResult) -> None:
        grades = self._grades.setdefault(grade.execution_id, [])
        grades.append(grade)

    async def get_grades(self, execution_id: str) -> list[GradeResult]:
        return list(self._grades.get(execution_id, []))
