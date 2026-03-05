"""
Transform Executor — reshapes data between nodes without LLM.

Implements B1 from the implementation plan.
SPEC references:
- §3.1: Controlled writes
- §3.5: TRANSFORM node contract

Supported operations: set, template, extract, cast.
Never uses eval() — template uses str.format_map(), extract uses dot-notation.
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import ExecutionState, NodeDefinition, TransformNodeConfig


class TransformExecutorError(Exception):
    pass


def _resolve_state_ref(ref: str, graph_state: dict[str, Any]) -> Any:
    """Resolve a 'state.field.nested' reference against graph_state."""
    if not ref.startswith("state."):
        raise TransformExecutorError(
            f"from_field must start with 'state.' (got '{ref}')"
        )
    parts = ref[len("state."):].split(".")
    value: Any = graph_state
    for part in parts:
        if not isinstance(value, dict):
            raise TransformExecutorError(
                f"Cannot traverse '{part}' in '{ref}' — parent is not a dict"
            )
        if part not in value:
            raise TransformExecutorError(
                f"Field '{ref}' not found in state (missing key '{part}')"
            )
        value = value[part]
    return value


def _resolve_dot_path(path: str, value: Any) -> Any:
    """Traverse a dot-notation path within a value (for 'extract' operation)."""
    if not path:
        return value
    parts = path.split(".")
    current = value
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                raise TransformExecutorError(
                    f"Extract path '{path}': key '{part}' not found"
                )
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                raise TransformExecutorError(
                    f"Extract path '{path}': '{part}' is not a valid list index"
                )
            try:
                current = current[idx]
            except IndexError:
                raise TransformExecutorError(
                    f"Extract path '{path}': index {idx} out of range"
                )
        else:
            raise TransformExecutorError(
                f"Extract path '{path}': cannot traverse into {type(current).__name__}"
            )
    return current


def _cast_value(value: Any, cast_type: str) -> Any:
    """Cast value to the specified type. Never uses eval()."""
    if cast_type == "str":
        return str(value)
    if cast_type == "int":
        return int(value)
    if cast_type == "float":
        return float(value)
    if cast_type == "bool":
        if isinstance(value, str):
            return value.lower() not in ("false", "0", "no", "")
        return bool(value)
    raise TransformExecutorError(f"Unknown cast type: '{cast_type}'")


import re as _re

_STATE_REF_PATTERN = _re.compile(r"\{(state\.[^}]+)\}")


def _render_template(template: str, graph_state: dict[str, Any]) -> str:
    """
    Render a template string by replacing {state.field} references with values.

    Uses regex substitution — never eval(). (SPEC §3.5)

    Example: "{state.first_name} {state.last_name}" → "John Doe"
    """
    def replacer(match: _re.Match) -> str:
        ref = match.group(1)  # e.g. "state.first_name"
        value = _resolve_state_ref(ref, graph_state)
        return str(value)

    return _STATE_REF_PATTERN.sub(replacer, template)


class TransformExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        config = TransformNodeConfig(**node_def.config)
        state_updates: dict[str, Any] = {}

        for op in config.operations:
            value: Any = None

            if op.from_field is not None:
                value = _resolve_state_ref(op.from_field, execution_state.graph_state)

            elif op.template is not None:
                # Regex-based template rendering — no eval() (SPEC §3.5)
                try:
                    value = _render_template(op.template, execution_state.graph_state)
                except TransformExecutorError as exc:
                    raise TransformExecutorError(
                        f"Template error in node '{node_id}': {exc}"
                    ) from exc

            if op.extract is not None and value is not None:
                value = _resolve_dot_path(op.extract, value)

            if op.cast is not None and value is not None:
                value = _cast_value(value, op.cast)

            state_updates[op.set] = value

        return state_updates
