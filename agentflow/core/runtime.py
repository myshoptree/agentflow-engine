"""
Execution Runtime — main loop that drives a compiled graph to completion.

SPEC references:
- §1.1: Deterministic execution
- §1.2: Execution isolation
- §1.3: Every execution reaches a terminal state
- §2.4: max_depth as infinite-loop guard
- §2.5: Edge priority and fallback
- §5.1: Retries with backoff
- §5.2: on_error routing before FAILED
- §5.3: global_timeout_seconds
- §6.1: HumanInputNode suspends execution
- §7.2: Minimum required events
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from agentflow.core.compiler import CompiledGraph
from agentflow.core.models import (
    ConditionNodeConfig,
    ExecutionState,
    ExecutionStatus,
    GuardrailNodeConfig,
    NodeExecutionRecord,
    NodeType,
    RetryPolicy,
    StartNodeConfig,
)
from agentflow.core.observability import StructuredLogger
from agentflow.core.state_manager import StateManagerProtocol
from agentflow.dsl.condition_parser import (
    ConditionEvaluationError,
    evaluate_cel_condition,
    evaluate_compound_condition,
    evaluate_condition,
)
from agentflow.executors.registry import get_executor


class RuntimeError_(Exception):
    """Internal runtime error (renamed to avoid shadowing built-in)."""


class NodeTimeoutError(Exception):
    """Raised when a node exceeds its per-node timeout (SPEC §5.4)."""


def _get_retry_policy(node_def: Any) -> RetryPolicy:
    """
    Extract RetryPolicy from node config.
    Precedence: explicit retry_policy > legacy max_retries field > defaults.
    """
    config = node_def.config or {}
    if config.get("retry_policy") is not None:
        return RetryPolicy(**config["retry_policy"])
    max_retries = int(config.get("max_retries", 0))
    return RetryPolicy(max_retries=max_retries)


def _calc_backoff(policy: RetryPolicy, attempt: int) -> float:
    return min(
        policy.backoff_base * (policy.backoff_multiplier ** (attempt - 1)),
        policy.backoff_max,
    )


class ExecutionRuntime:
    def __init__(self, state_manager: StateManagerProtocol) -> None:
        self._sm = state_manager

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        compiled_graph: CompiledGraph,
        execution_state: ExecutionState,
    ) -> ExecutionState:
        """
        Drive execution_state through compiled_graph until a terminal state.
        Returns the final ExecutionState.
        """
        log = StructuredLogger(execution_state.execution_id, compiled_graph.definition.id)
        definition = compiled_graph.definition

        execution_state.status = ExecutionStatus.RUNNING
        await self._sm.save_execution(execution_state)

        log.execution_started(
            entry_node=execution_state.current_node or definition.entry_node,
            initial_state=execution_state.graph_state,
        )

        start_time = time.monotonic()

        try:
            while True:
                # --- Global timeout check (SPEC §5.3) ---
                elapsed = time.monotonic() - start_time
                if elapsed >= definition.global_timeout_seconds:
                    log.execution_timed_out(execution_state.depth)
                    await self._sm.update_status(
                        execution_state.execution_id, ExecutionStatus.TIMED_OUT
                    )
                    execution_state.status = ExecutionStatus.TIMED_OUT
                    return execution_state

                # --- Depth guard (SPEC §2.4) ---
                if execution_state.depth >= definition.max_depth:
                    reason = f"max_depth ({definition.max_depth}) exceeded"
                    log.execution_failed(reason)
                    await self._sm.update_status(
                        execution_state.execution_id,
                        ExecutionStatus.FAILED,
                        error_message=reason,
                    )
                    execution_state.status = ExecutionStatus.FAILED
                    execution_state.error_message = reason
                    return execution_state

                node_id = execution_state.current_node
                if node_id is None:
                    raise RuntimeError_("current_node is None — cannot continue")

                compiled_node = compiled_graph.nodes.get(node_id)
                if compiled_node is None:
                    raise RuntimeError_(f"Node '{node_id}' not found in compiled graph")

                node_def = compiled_node.definition

                # --- START node (B2) — declarative entry point, inject input_as_text ---
                if node_def.type == NodeType.START:
                    start_cfg = StartNodeConfig(**node_def.config)
                    for inp in start_cfg.inputs:
                        if inp.as_text:
                            raw = execution_state.graph_state.get(inp.name, "")
                            execution_state.graph_state["input_as_text"] = str(raw)
                    next_node = self._resolve_transition(
                        node_id, compiled_node.outgoing_edges, execution_state, log
                    )
                    if next_node is None:
                        reason = f"START node '{node_id}' has no outgoing edge"
                        execution_state.status = ExecutionStatus.FAILED
                        execution_state.error_message = reason
                        await self._sm.update_status(
                            execution_state.execution_id,
                            ExecutionStatus.FAILED,
                            error_message=reason,
                        )
                        return execution_state
                    execution_state.current_node = next_node
                    execution_state.depth += 1
                    await self._sm.save_execution(execution_state)
                    continue

                # --- END node (SPEC §2.5) ---
                if node_def.type == NodeType.END:
                    checkpoint = await self._sm.checkpoint(execution_state, node_id)
                    log.state_checkpoint(
                        node_id=node_id,
                        checkpoint_id=checkpoint.checkpoint_id,
                        depth=execution_state.depth,
                        state_keys=list(execution_state.graph_state.keys()),
                    )
                    log.execution_completed(execution_state.graph_state, execution_state.depth)
                    await self._sm.update_status(
                        execution_state.execution_id, ExecutionStatus.COMPLETED
                    )
                    execution_state.status = ExecutionStatus.COMPLETED
                    return execution_state

                # --- HumanInput node — suspend (SPEC §6.1) ---
                if node_def.type == NodeType.HUMAN_INPUT:
                    if execution_state.resuming:
                        # Already resumed — input is in graph_state, resolve transition normally
                        execution_state.resuming = False
                        t0_resume = time.monotonic()
                        next_node = self._resolve_transition(
                            node_id, compiled_node.outgoing_edges, execution_state, log
                        )
                        duration_ms_resume = (time.monotonic() - t0_resume) * 1000
                        record_resume = NodeExecutionRecord(
                            execution_id=execution_state.execution_id,
                            node_id=node_id,
                            node_type=node_def.type,
                            attempt=1,
                            input_state=dict(execution_state.graph_state),
                            output_data={},
                            duration_ms=duration_ms_resume,
                            started_at=datetime.now(timezone.utc),
                            completed_at=datetime.now(timezone.utc),
                        )
                        await self._sm.record_node_execution(record_resume)
                        log.node_completed(
                            node_id,
                            node_def.type.value,
                            duration_ms=duration_ms_resume,
                            attempt=1,
                            state_updates={},
                        )
                        if next_node is None:
                            execution_state.status = ExecutionStatus.COMPLETED
                            await self._sm.update_status(
                                execution_state.execution_id, ExecutionStatus.COMPLETED
                            )
                            return execution_state
                        execution_state.current_node = next_node
                        execution_state.depth += 1
                        await self._sm.save_execution(execution_state)
                        continue
                    # First pass — emit trace, record started, suspend
                    t0_suspend = time.monotonic()
                    log.node_started(
                        node_id,
                        node_def.type.value,
                        attempt=1,
                        input_state=dict(execution_state.graph_state),
                    )
                    record_suspend = NodeExecutionRecord(
                        execution_id=execution_state.execution_id,
                        node_id=node_id,
                        node_type=node_def.type,
                        attempt=1,
                        input_state=dict(execution_state.graph_state),
                        duration_ms=(time.monotonic() - t0_suspend) * 1000,
                        started_at=datetime.now(timezone.utc),
                    )
                    await self._sm.record_node_execution(record_suspend)
                    execution_state.current_node = node_id
                    log.execution_suspended(node_id)
                    await self._sm.update_status(
                        execution_state.execution_id, ExecutionStatus.SUSPENDED
                    )
                    execution_state.status = ExecutionStatus.SUSPENDED
                    await self._sm.save_execution(execution_state)
                    return execution_state

                # --- Execute node with retry (SPEC §5.1) ---
                executor = get_executor(node_def.type, compiled_graph)
                retry_policy = _get_retry_policy(node_def)
                node_timeout = node_def.config.get("timeout_seconds") if node_def.config else None
                state_updates: dict[str, Any] | None = None
                last_error: Exception | None = None

                for attempt in range(1, retry_policy.max_retries + 2):  # +1 for initial attempt
                    log.node_started(
                        node_id,
                        node_def.type.value,
                        attempt=attempt,
                        input_state=dict(execution_state.graph_state),
                    )
                    t0 = time.monotonic()
                    record = NodeExecutionRecord(
                        execution_id=execution_state.execution_id,
                        node_id=node_id,
                        node_type=node_def.type,
                        attempt=attempt,
                        input_state=dict(execution_state.graph_state),
                        started_at=datetime.now(timezone.utc),
                    )
                    try:
                        coro = executor.execute(node_id, node_def, execution_state)
                        if node_timeout is not None:
                            try:
                                state_updates = await asyncio.wait_for(coro, timeout=node_timeout)
                            except asyncio.TimeoutError:
                                raise NodeTimeoutError(
                                    f"Node '{node_id}' timed out after {node_timeout}s (SPEC §5.4)"
                                )
                        else:
                            state_updates = await coro

                        duration_ms = (time.monotonic() - t0) * 1000
                        record.output_data = state_updates
                        record.duration_ms = duration_ms
                        record.completed_at = datetime.now(timezone.utc)
                        await self._sm.record_node_execution(record)

                        log.node_completed(
                            node_id,
                            node_def.type.value,
                            duration_ms=duration_ms,
                            attempt=attempt,
                            state_updates=state_updates or {},
                        )
                        last_error = None
                        break

                    except NodeTimeoutError as exc:
                        # Timeout is non-retryable (SPEC §5.4)
                        duration_ms = (time.monotonic() - t0) * 1000
                        last_error = exc
                        record.error_message = str(exc)
                        record.duration_ms = duration_ms
                        record.completed_at = datetime.now(timezone.utc)
                        await self._sm.record_node_execution(record)
                        log.node_failed(node_id, node_def.type.value, str(exc), attempt)
                        break  # no retry on timeout

                    except Exception as exc:
                        duration_ms = (time.monotonic() - t0) * 1000
                        last_error = exc
                        record.error_message = str(exc)
                        record.duration_ms = duration_ms
                        record.completed_at = datetime.now(timezone.utc)
                        await self._sm.record_node_execution(record)

                        log.node_failed(node_id, node_def.type.value, str(exc), attempt)

                        if attempt <= retry_policy.max_retries:
                            backoff = _calc_backoff(retry_policy, attempt)
                            log.node_retrying(
                                node_id, node_def.type.value, attempt + 1, backoff, str(exc)
                            )
                            await asyncio.sleep(backoff)
                        # else: loop ends, last_error is set

                # --- Handle unrecoverable failure (SPEC §5.2) ---
                if last_error is not None:
                    on_error_node = node_def.on_error
                    if on_error_node:
                        execution_state.current_node = on_error_node
                        execution_state.depth += 1
                        await self._sm.save_execution(execution_state)
                        continue  # re-enter loop at error handler node

                    error_msg = f"Node '{node_id}' failed after {retry_policy.max_retries + 1} attempt(s): {last_error}"
                    log.execution_failed(error_msg, node_id)
                    await self._sm.update_status(
                        execution_state.execution_id,
                        ExecutionStatus.FAILED,
                        error_message=error_msg,
                    )
                    execution_state.status = ExecutionStatus.FAILED
                    execution_state.error_message = error_msg
                    return execution_state

                # --- Apply state updates (SPEC §3.1 — only declared fields) ---
                if state_updates:
                    execution_state.graph_state.update(state_updates)

                # --- Checkpoint (SPEC §3.2) ---
                checkpoint = await self._sm.checkpoint(execution_state, node_id)
                log.state_checkpoint(
                    node_id=node_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                    depth=execution_state.depth,
                    state_keys=list(execution_state.graph_state.keys()),
                )

                # --- Transition resolution (SPEC §2.5) ---
                # GUARDRAIL: check result and route to on_fail if needed (SPEC §11.3)
                if node_def.type == NodeType.GUARDRAIL and state_updates:
                    guardrail_cfg = GuardrailNodeConfig(**node_def.config)
                    guardrail_result = state_updates.get("result", "pass")
                    if guardrail_result == "fail":
                        next_node = guardrail_cfg.on_fail
                        log.transition_resolved(
                            from_node=node_id,
                            to_node=next_node,
                            condition_matched=True,
                            condition_field="guardrail.result",
                        )
                    else:
                        next_node = self._resolve_transition(
                            node_id, compiled_node.outgoing_edges, execution_state, log
                        )
                # ConditionNode with explicit branches takes priority over edge-based routing
                elif (
                    node_def.type == NodeType.CONDITION
                    and node_def.config.get("branches")
                ):
                    next_node = self._resolve_condition_node(
                        node_id, node_def, execution_state.graph_state, log
                    )
                else:
                    next_node = self._resolve_transition(
                        node_id, compiled_node.outgoing_edges, execution_state, log
                    )

                if next_node is None:
                    reason = f"No valid transition from node '{node_id}'"
                    log.execution_failed(reason, node_id)
                    await self._sm.update_status(
                        execution_state.execution_id,
                        ExecutionStatus.FAILED,
                        error_message=reason,
                    )
                    execution_state.status = ExecutionStatus.FAILED
                    execution_state.error_message = reason
                    return execution_state

                execution_state.current_node = next_node
                execution_state.depth += 1
                await self._sm.save_execution(execution_state)

        except Exception as exc:
            error_msg = f"Unexpected runtime error: {exc}"
            log.execution_failed(error_msg)
            await self._sm.update_status(
                execution_state.execution_id,
                ExecutionStatus.FAILED,
                error_message=error_msg,
            )
            execution_state.status = ExecutionStatus.FAILED
            execution_state.error_message = error_msg
            return execution_state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_condition_node(
        self,
        node_id: str,
        node_def: Any,
        graph_state: dict[str, Any],
        log: StructuredLogger,
    ) -> str | None:
        """
        Resolve the next node for a ConditionNode with explicit branches.
        Evaluates branches in order; returns config.default if none match, or None.
        """
        config = ConditionNodeConfig(**node_def.config)
        for branch in config.branches:
            try:
                matched = evaluate_compound_condition(branch.condition, graph_state)
            except ConditionEvaluationError:
                matched = False
            if matched:
                log.transition_resolved(
                    from_node=node_id,
                    to_node=branch.target,
                    condition_matched=True,
                )
                return branch.target

        if config.default is not None:
            log.transition_resolved(
                from_node=node_id,
                to_node=config.default,
                condition_matched=False,
            )
            return config.default

        log.no_valid_transition(node_id)
        return None

    def _resolve_transition(
        self,
        from_node: str,
        edges: list,
        execution_state: ExecutionState,
        log: StructuredLogger,
    ) -> str | None:
        """
        Evaluate outgoing edges in priority order (highest first).
        First matching conditional edge wins. Unconditional edge is fallback.
        Returns next node_id or None if no transition applies (SPEC §2.5).
        """
        fallback: str | None = None

        for edge in edges:
            # CEL condition (SPEC §4.4)
            if edge.condition_language == "cel" and edge.condition_cel is not None:
                try:
                    matched = evaluate_cel_condition(edge.condition_cel, execution_state.graph_state)
                except Exception:
                    matched = False
                    log.warn_cel_evaluation_error(edge.from_node, edge.condition_cel)
                if matched:
                    log.transition_resolved(
                        from_node=from_node,
                        to_node=edge.to_node,
                        condition_matched=True,
                    )
                    return edge.to_node
                continue

            if edge.condition is None:
                # Unconditional — keep as fallback, don't take immediately
                if fallback is None:
                    fallback = edge.to_node
                continue

            try:
                matched = evaluate_condition(edge.condition, execution_state.graph_state)
            except ConditionEvaluationError:
                matched = False

            if matched:
                log.transition_resolved(
                    from_node=from_node,
                    to_node=edge.to_node,
                    condition_matched=True,
                    condition_field=edge.condition.field,
                )
                return edge.to_node

        # No conditional edge matched — use fallback if available
        if fallback is not None:
            log.transition_resolved(
                from_node=from_node,
                to_node=fallback,
                condition_matched=False,
            )
            return fallback

        log.no_valid_transition(from_node)
        return None

