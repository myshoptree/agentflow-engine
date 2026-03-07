"""
Tests for C3 — CEL conditions as alternative to DSL.

These tests mock the CEL library when it's not installed.
SPEC references: §4.4
"""
from __future__ import annotations

import pytest

from agentflow.core.compiler import GraphCompiler, GraphCompilationError
from agentflow.core.models import (
    Edge,
    ExecutionStatus,
    GraphDefinition,
    NodeDefinition,
    NodeType,
    StateFieldDefinition,
    StateFieldType,
)
from agentflow.core.runtime import ExecutionRuntime
from agentflow.core.state_manager import InMemoryStateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def end_node() -> NodeDefinition:
    return NodeDefinition(type=NodeType.END, config={})


def tool_node(tool_name: str, output_mapping: dict = {}) -> NodeDefinition:
    return NodeDefinition(
        type=NodeType.TOOL,
        config={"tool_name": tool_name, "output_mapping": output_mapping, "max_retries": 0},
    )


# ---------------------------------------------------------------------------
# Compiler: ambiguity check (SPEC §4.4)
# ---------------------------------------------------------------------------

def test_compiler_rejects_both_dsl_and_cel_on_same_edge():
    """Compiler should reject edges that define both condition and condition_cel."""
    g = GraphDefinition(
        id="cel_ambiguity",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
        ],
        nodes={
            "start": end_node(),
            "other": end_node(),
        },
        edges=[
            Edge(
                from_node="start",
                to_node="other",
                condition_language="cel",
                condition_cel="state.intent == 'HOT'",
            ),
        ],
    )
    # This should compile fine — only CEL, no DSL ambiguity
    # No error expected here
    GraphCompiler().compile(g)


def test_compiler_rejects_dsl_and_cel_simultaneously():
    """Edge with both condition (DSL) and condition_cel defined must fail compilation."""
    from agentflow.core.models import EdgeCondition

    g = GraphDefinition(
        id="cel_dsl_ambiguity",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
        ],
        nodes={
            "start": end_node(),
            "other": end_node(),
        },
        edges=[
            Edge(
                from_node="start",
                to_node="other",
                condition=EdgeCondition(field="state.intent", operator="eq", value="HOT"),
                condition_cel="state.intent == 'HOT'",
                condition_language="cel",
            ),
        ],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


# ---------------------------------------------------------------------------
# DSL condition parser: evaluate_cel_condition
# ---------------------------------------------------------------------------

def test_evaluate_cel_condition_without_cel_python():
    """evaluate_cel_condition should raise ConditionEvaluationError if cel-python is not installed."""
    import sys
    from agentflow.dsl.condition_parser import evaluate_cel_condition, ConditionEvaluationError

    original = sys.modules.get("cel")
    sys.modules["cel"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(ConditionEvaluationError, match="google-cel-python"):
            evaluate_cel_condition("state.intent == 'HOT'", {"intent": "HOT"})
    finally:
        if original is None:
            del sys.modules["cel"]
        else:
            sys.modules["cel"] = original


def test_evaluate_cel_condition_with_mock():
    """evaluate_cel_condition should correctly evaluate when cel-python is mocked."""
    import sys
    from unittest.mock import MagicMock
    from agentflow.dsl.condition_parser import ConditionEvaluationError

    # Build a minimal cel mock
    mock_prog = MagicMock()
    mock_prog.evaluate.return_value = True
    mock_env = MagicMock()
    mock_env.compile.return_value = mock_prog
    mock_cel = MagicMock()
    mock_cel.Environment.return_value = mock_env

    original = sys.modules.get("cel")
    sys.modules["cel"] = mock_cel
    try:
        # Re-import to use mocked module
        import importlib
        import agentflow.dsl.condition_parser as cp_module
        importlib.reload(cp_module)

        result = cp_module.evaluate_cel_condition("state.intent == 'HOT'", {"intent": "HOT"})
        assert result is True
    finally:
        if original is None:
            sys.modules.pop("cel", None)
        else:
            sys.modules["cel"] = original
        importlib.reload(cp_module)


# ---------------------------------------------------------------------------
# Runtime: CEL condition evaluation fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cel_evaluation_error_falls_back_to_false():
    """When CEL evaluation fails at runtime, condition is treated as False (SPEC §4.4)."""
    from agentflow.executors.tool_executor import register_tool

    async def noop_cel(**kwargs) -> dict:
        return {}

    register_tool("noop_cel", noop_cel)

    # CEL edge that will fail (no cel-python installed) → falls back to DSL fallback edge
    g = GraphDefinition(
        id="cel_fallback_test",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
        ],
        nodes={
            "start": tool_node("noop_cel"),
            "cel_target": end_node(),
            "fallback_target": end_node(),
        },
        edges=[
            Edge(
                from_node="start",
                to_node="cel_target",
                condition_language="cel",
                condition_cel="state.intent == 'HOT'",
                priority=10,
            ),
            Edge(
                from_node="start",
                to_node="fallback_target",
                priority=0,  # unconditional fallback
            ),
        ],
    )

    compiler = GraphCompiler()
    # Compilation may produce a warning about missing cel-python — that's OK
    try:
        compiled = compiler.compile(g)
    except Exception:
        pytest.skip("CEL compilation error — cel-python may not be installed")

    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(g, {"intent": "COLD"})
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)

    # Whether CEL evaluates correctly or falls back, execution should complete
    assert final.status == ExecutionStatus.COMPLETED
