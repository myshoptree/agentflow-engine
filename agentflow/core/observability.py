"""
Observability Layer — structured JSON event logging.

Every execution emits a defined set of events (SPEC §7.2).
Events must never be silently lost (SPEC §7.3).

Event types:
  execution.started / execution.completed / execution.failed
  node.started / node.completed / node.failed / node.retrying
  transition.resolved
  state.checkpoint
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("agentflow.observability")


# ---------------------------------------------------------------------------
# Event builder helpers
# ---------------------------------------------------------------------------

def _base(
    event_type: str,
    execution_id: str,
    graph_id: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "execution_id": execution_id,
        "graph_id": graph_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _emit(event: dict[str, Any]) -> None:
    """
    Emit a structured JSON event. If the logger handler fails, we fall back
    to stderr to avoid silent loss (SPEC §7.3).
    """
    try:
        logger.info(json.dumps(event))
    except Exception as e:  # noqa: BLE001
        # Last-resort fallback — never suppress silently (SPEC §7.3)
        try:
            print(json.dumps({"event_type": "observability.error", "error": str(e), **event}), file=sys.stderr)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class StructuredLogger:
    def __init__(self, execution_id: str, graph_id: str) -> None:
        self.execution_id = execution_id
        self.graph_id = graph_id

    # -- Execution events --

    def execution_started(self, entry_node: str, initial_state: dict[str, Any]) -> None:
        _emit(_base(
            "execution.started",
            self.execution_id,
            self.graph_id,
            entry_node=entry_node,
            initial_state_keys=list(initial_state.keys()),
        ))

    def execution_completed(self, final_state: dict[str, Any], depth: int) -> None:
        _emit(_base(
            "execution.completed",
            self.execution_id,
            self.graph_id,
            depth=depth,
            final_state_keys=list(final_state.keys()),
        ))

    def execution_failed(self, reason: str, node_id: str | None = None) -> None:
        _emit(_base(
            "execution.failed",
            self.execution_id,
            self.graph_id,
            reason=reason,
            node_id=node_id,
        ))

    def execution_timed_out(self, depth: int) -> None:
        _emit(_base(
            "execution.timed_out",
            self.execution_id,
            self.graph_id,
            depth=depth,
        ))

    def execution_suspended(self, node_id: str) -> None:
        _emit(_base(
            "execution.suspended",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
        ))

    # -- Node events --

    def node_started(
        self,
        node_id: str,
        node_type: str,
        attempt: int = 1,
        input_state: dict[str, Any] | None = None,
    ) -> None:
        _emit(_base(
            "node.started",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
            node_type=node_type,
            attempt=attempt,
            input_state=input_state or {},
        ))

    def node_completed(
        self,
        node_id: str,
        node_type: str,
        duration_ms: float,
        attempt: int = 1,
        llm_tokens_used: int = 0,
        state_updates: dict[str, Any] | None = None,
    ) -> None:
        _emit(_base(
            "node.completed",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
            node_type=node_type,
            attempt=attempt,
            duration_ms=round(duration_ms, 1),
            llm_tokens_used=llm_tokens_used,
            state_updates=state_updates or {},
        ))

    def node_failed(
        self,
        node_id: str,
        node_type: str,
        error: str,
        attempt: int = 1,
    ) -> None:
        _emit(_base(
            "node.failed",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
            node_type=node_type,
            attempt=attempt,
            error=error,
        ))

    def node_retrying(
        self,
        node_id: str,
        node_type: str,
        attempt: int,
        backoff_seconds: float,
        error: str,
    ) -> None:
        _emit(_base(
            "node.retrying",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
            node_type=node_type,
            attempt=attempt,
            backoff_seconds=backoff_seconds,
            error=error,
        ))

    # -- Transition events --

    def transition_resolved(
        self,
        from_node: str,
        to_node: str,
        condition_matched: bool,
        condition_field: str | None = None,
    ) -> None:
        _emit(_base(
            "transition.resolved",
            self.execution_id,
            self.graph_id,
            from_node=from_node,
            to_node=to_node,
            condition_matched=condition_matched,
            condition_field=condition_field,
        ))

    def no_valid_transition(self, from_node: str) -> None:
        _emit(_base(
            "transition.no_valid_transition",
            self.execution_id,
            self.graph_id,
            from_node=from_node,
        ))

    # -- State checkpoint events --

    def state_checkpoint(
        self,
        node_id: str,
        checkpoint_id: str,
        depth: int,
        state_keys: list[str],
    ) -> None:
        _emit(_base(
            "state.checkpoint",
            self.execution_id,
            self.graph_id,
            node_id=node_id,
            checkpoint_id=checkpoint_id,
            depth=depth,
            state_keys=state_keys,
        ))
