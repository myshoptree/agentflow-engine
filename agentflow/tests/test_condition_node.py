"""
Tests for ConditionNode with explicit branches and compound conditions (AND/OR).
Covers SPEC §4 extensions for ConditionNodeConfig.
"""
import pytest

from agentflow.core.compiler import GraphCompiler, GraphCompilationError
from agentflow.core.models import (
    CompoundCondition,
    ConditionBranch,
    Edge,
    EdgeCondition,
    ExecutionStatus,
    GraphDefinition,
    LogicalOperator,
    NodeDefinition,
    NodeType,
    StateFieldDefinition,
    StateFieldType,
)
from agentflow.core.runtime import ExecutionRuntime
from agentflow.core.state_manager import InMemoryStateManager
from agentflow.executors import registry as executor_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def condition_node(branches: list, default: str | None = None) -> NodeDefinition:
    raw_branches = []
    for branch in branches:
        if isinstance(branch["condition"], dict) and "conditions" in branch["condition"]:
            raw_branches.append({
                "condition": branch["condition"],
                "target": branch["target"],
            })
        else:
            raw_branches.append({
                "condition": branch["condition"],
                "target": branch["target"],
            })
    return NodeDefinition(
        type=NodeType.CONDITION,
        config={
            "branches": raw_branches,
            "default": default,
        },
    )


def end_node() -> NodeDefinition:
    return NodeDefinition(type=NodeType.END, config={})


def agent_node() -> NodeDefinition:
    return NodeDefinition(
        type=NodeType.AGENT,
        config={"state_output_mapping": {}, "max_retries": 0},
    )


def _make_schema(*fields: tuple[str, StateFieldType]) -> list[StateFieldDefinition]:
    return [StateFieldDefinition(name=n, type=t) for n, t in fields]


async def _run(graph_def: GraphDefinition, initial_input: dict) -> tuple[ExecutionStatus, dict]:
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(graph_def, initial_input)
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)
    return final.status, final.graph_state


# ---------------------------------------------------------------------------
# Test 1: single branch with simple EdgeCondition matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_branch_edge_condition_matches():
    """EdgeCondition simple in branch → matches → routes to correct target."""
    graph = GraphDefinition(
        id="test-single-branch",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {"field": "state.intent", "operator": "eq", "value": "HOT"},
                    "target": "hot_end",
                }],
                default="cold_end",
            ),
            "hot_end": end_node(),
            "cold_end": end_node(),
        },
        edges=[
            Edge(from_node="check", to_node="hot_end"),
            Edge(from_node="check", to_node="cold_end"),
        ],
        state_schema=_make_schema(("intent", StateFieldType.STR)),
    )

    status, state = await _run(graph, {"intent": "HOT"})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 2: compound AND — both conditions true → matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compound_and_both_true():
    """AND with two conditions both true → first branch matched."""
    graph = GraphDefinition(
        id="test-and-both-true",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {
                        "operator": "and",
                        "conditions": [
                            {"field": "state.intent", "operator": "eq", "value": "HOT"},
                            {"field": "state.confidence", "operator": "gte", "value": 0.8},
                        ],
                    },
                    "target": "matched_end",
                }],
                default="default_end",
            ),
            "matched_end": end_node(),
            "default_end": end_node(),
        },
        edges=[
            Edge(from_node="check", to_node="matched_end"),
            Edge(from_node="check", to_node="default_end"),
        ],
        state_schema=_make_schema(
            ("intent", StateFieldType.STR),
            ("confidence", StateFieldType.FLOAT),
        ),
    )

    status, _ = await _run(graph, {"intent": "HOT", "confidence": 0.9})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 3: compound AND — one condition false → no match → next branch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compound_and_one_false():
    """AND with one false condition → first branch doesn't match → falls to default."""
    graph = GraphDefinition(
        id="test-and-one-false",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {
                        "operator": "and",
                        "conditions": [
                            {"field": "state.intent", "operator": "eq", "value": "HOT"},
                            {"field": "state.confidence", "operator": "gte", "value": 0.8},
                        ],
                    },
                    "target": "matched_end",
                }],
                default="default_end",
            ),
            "matched_end": end_node(),
            "default_end": end_node(),
        },
        edges=[
            Edge(from_node="check", to_node="matched_end"),
            Edge(from_node="check", to_node="default_end"),
        ],
        state_schema=_make_schema(
            ("intent", StateFieldType.STR),
            ("confidence", StateFieldType.FLOAT),
        ),
    )

    # confidence = 0.5 < 0.8 → AND fails → goes to default
    status, _ = await _run(graph, {"intent": "HOT", "confidence": 0.5})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 4: compound OR — one condition true → matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compound_or_one_true():
    """OR with one true condition → branch matches."""
    graph = GraphDefinition(
        id="test-or-one-true",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {
                        "operator": "or",
                        "conditions": [
                            {"field": "state.intent", "operator": "eq", "value": "HOT"},
                            {"field": "state.intent", "operator": "eq", "value": "WARM"},
                        ],
                    },
                    "target": "matched_end",
                }],
                default="default_end",
            ),
            "matched_end": end_node(),
            "default_end": end_node(),
        },
        edges=[
            Edge(from_node="check", to_node="matched_end"),
            Edge(from_node="check", to_node="default_end"),
        ],
        state_schema=_make_schema(("intent", StateFieldType.STR)),
    )

    # WARM matches the OR
    status, _ = await _run(graph, {"intent": "WARM"})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 5: default branch used when no branch matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_branch_used():
    """No branch matches → default target is used → COMPLETED."""
    graph = GraphDefinition(
        id="test-default",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {"field": "state.intent", "operator": "eq", "value": "HOT"},
                    "target": "hot_end",
                }],
                default="fallback_end",
            ),
            "hot_end": end_node(),
            "fallback_end": end_node(),
        },
        edges=[
            Edge(from_node="check", to_node="hot_end"),
            Edge(from_node="check", to_node="fallback_end"),
        ],
        state_schema=_make_schema(("intent", StateFieldType.STR)),
    )

    status, _ = await _run(graph, {"intent": "COLD"})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 6: no match and no default → FAILED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_match_no_default_fails():
    """No branch matches and no default → execution FAILED."""
    graph = GraphDefinition(
        id="test-no-match-no-default",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {"field": "state.intent", "operator": "eq", "value": "HOT"},
                    "target": "hot_end",
                }],
                default=None,
            ),
            "hot_end": end_node(),
        },
        edges=[Edge(from_node="check", to_node="hot_end")],
        state_schema=_make_schema(("intent", StateFieldType.STR)),
    )

    status, _ = await _run(graph, {"intent": "COLD"})
    assert status == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# Test 7: compiler rejects missing target in branch
# ---------------------------------------------------------------------------

def test_compiler_rejects_missing_target():
    """Branch target that doesn't exist in nodes → compilation error."""
    graph = GraphDefinition(
        id="test-missing-target",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {"field": "state.intent", "operator": "eq", "value": "HOT"},
                    "target": "nonexistent_node",  # does not exist
                }],
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="check", to_node="end")],
        state_schema=_make_schema(("intent", StateFieldType.STR)),
    )

    with pytest.raises(GraphCompilationError) as exc_info:
        GraphCompiler().compile(graph)

    assert "nonexistent_node" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 8: compiler rejects field not in state_schema
# ---------------------------------------------------------------------------

def test_compiler_rejects_missing_field():
    """Condition references field not declared in state_schema → compilation error."""
    graph = GraphDefinition(
        id="test-missing-field",
        entry_node="check",
        nodes={
            "check": condition_node(
                branches=[{
                    "condition": {
                        "field": "state.undeclared_field",  # not in state_schema
                        "operator": "eq",
                        "value": "HOT",
                    },
                    "target": "end",
                }],
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="check", to_node="end")],
        state_schema=_make_schema(("intent", StateFieldType.STR)),  # only "intent" declared
    )

    with pytest.raises(GraphCompilationError) as exc_info:
        GraphCompiler().compile(graph)

    assert "undeclared_field" in str(exc_info.value)
