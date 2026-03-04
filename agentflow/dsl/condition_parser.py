"""
Safe DSL evaluator for EdgeCondition.

Never uses eval() or exec(). Supports a fixed set of operators defined
in SPEC.md §4.1: eq, neq, gt, lt, gte, lte, in, contains, is_null.
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import CompoundCondition, EdgeCondition, LogicalOperator


class ConditionEvaluationError(Exception):
    pass


def _resolve_field(field: str, graph_state: dict[str, Any]) -> Any:
    """
    Resolve a dot-notation field path against graph_state.

    "state.intent"     → graph_state["intent"]
    "state.score"      → graph_state["score"]
    "state.meta.tag"   → graph_state["meta"]["tag"]

    The leading "state." prefix is stripped; subsequent segments
    traverse nested dicts.
    """
    if not field.startswith("state."):
        raise ConditionEvaluationError(
            f"Field '{field}' must start with 'state.' (got: {field!r})"
        )

    parts = field[len("state."):].split(".")
    value: Any = graph_state

    for part in parts:
        if not isinstance(value, dict):
            raise ConditionEvaluationError(
                f"Cannot traverse '{part}' — parent is not a dict in field '{field}'"
            )
        if part not in value:
            raise ConditionEvaluationError(
                f"Field '{field}' not found in state (missing key: '{part}')"
            )
        value = value[part]

    return value


def evaluate_condition(condition: EdgeCondition, graph_state: dict[str, Any]) -> bool:
    """
    Evaluate an EdgeCondition against the current graph_state.

    Returns True if the condition is satisfied, False otherwise.
    Raises ConditionEvaluationError on invalid field paths or unsupported operators.
    """
    actual = _resolve_field(condition.field, graph_state)
    op = condition.operator
    expected = condition.value

    if op == "eq":
        return actual == expected

    if op == "neq":
        return actual != expected

    if op == "gt":
        _assert_comparable(actual, expected, op)
        return actual > expected

    if op == "lt":
        _assert_comparable(actual, expected, op)
        return actual < expected

    if op == "gte":
        _assert_comparable(actual, expected, op)
        return actual >= expected

    if op == "lte":
        _assert_comparable(actual, expected, op)
        return actual <= expected

    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            raise ConditionEvaluationError(
                f"Operator 'in' requires value to be a list, got {type(expected).__name__}"
            )
        return actual in expected

    if op == "contains":
        if not isinstance(actual, (str, list, tuple, set)):
            raise ConditionEvaluationError(
                f"Operator 'contains' requires field to be str or list, got {type(actual).__name__}"
            )
        return expected in actual

    if op == "is_null":
        return actual is None

    # Unreachable if EdgeCondition validation is correct, but guard anyway
    raise ConditionEvaluationError(f"Unknown operator: '{op}'")


def evaluate_compound_condition(
    condition: CompoundCondition | EdgeCondition,
    graph_state: dict[str, Any],
) -> bool:
    """
    Evaluate a CompoundCondition (AND/OR) or a simple EdgeCondition against graph_state.
    """
    if isinstance(condition, EdgeCondition):
        return evaluate_condition(condition, graph_state)
    results = [evaluate_condition(c, graph_state) for c in condition.conditions]
    if condition.operator == LogicalOperator.AND:
        return all(results)
    return any(results)  # OR


def _assert_comparable(actual: Any, expected: Any, op: str) -> None:
    if type(actual) not in (int, float) or type(expected) not in (int, float):
        raise ConditionEvaluationError(
            f"Operator '{op}' requires numeric values, "
            f"got {type(actual).__name__} and {type(expected).__name__}"
        )
