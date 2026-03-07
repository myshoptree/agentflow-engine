"""
Tests for GraphCompiler — validates SPEC §2 and §4 enforcement.
"""
import pytest

from agentflow.core.compiler import GraphCompiler, GraphCompilationError
from agentflow.core.models import (
    Edge,
    EdgeCondition,
    GraphDefinition,
    NodeDefinition,
    NodeType,
    StateFieldDefinition,
    StateFieldType,
)


def make_node(t: NodeType = NodeType.AGENT) -> NodeDefinition:
    return NodeDefinition(type=t, config={})


def end_node() -> NodeDefinition:
    return NodeDefinition(type=NodeType.END, config={})


compiler = GraphCompiler()


# ---------------------------------------------------------------------------
# Valid graphs compile without errors
# ---------------------------------------------------------------------------

def test_simple_linear_graph_compiles():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": end_node()},
        edges=[Edge(from_node="a", to_node="b")],
    )
    compiled = compiler.compile(g)
    assert "a" in compiled.nodes
    assert "b" in compiled.nodes


def test_edges_sorted_by_priority():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": end_node(), "c": end_node()},
        edges=[
            Edge(from_node="a", to_node="b", priority=0),
            Edge(from_node="a", to_node="c", priority=10),
        ],
    )
    compiled = compiler.compile(g)
    # Higher priority first
    assert compiled.nodes["a"].outgoing_edges[0].to_node == "c"
    assert compiled.nodes["a"].outgoing_edges[1].to_node == "b"


# ---------------------------------------------------------------------------
# SPEC §2.3 — unreachable nodes → warning, not error
# ---------------------------------------------------------------------------

def test_unreachable_node_produces_warning():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": end_node(), "orphan": make_node()},
        edges=[],
    )
    compiled = compiler.compile(g)
    warnings = [w for w in compiled.compilation_warnings if "orphan" in w.message]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"


# ---------------------------------------------------------------------------
# SPEC §2 — invalid edge references → error
# ---------------------------------------------------------------------------

def test_missing_from_node_raises():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": end_node()},
        edges=[Edge(from_node="nonexistent", to_node="a")],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "nonexistent" in str(exc_info.value)


def test_missing_to_node_raises():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node()},
        edges=[Edge(from_node="a", to_node="nonexistent")],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SPEC §2.4 — unconditional cycles → error
# ---------------------------------------------------------------------------

def test_unconditional_cycle_raises():
    """A ↔ B with no conditions — infinite loop."""
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": make_node()},
        edges=[
            Edge(from_node="a", to_node="b"),  # no condition
            Edge(from_node="b", to_node="a"),  # no condition
        ],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "Infinite cycle" in str(exc_info.value)


def test_conditional_cycle_is_valid():
    """A → B → A (conditional) is valid — it has an exit path."""
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": make_node(), "end": end_node()},
        state_schema=[StateFieldDefinition(name="score", type=StateFieldType.FLOAT)],
        edges=[
            Edge(from_node="a", to_node="b"),
            Edge(
                from_node="b",
                to_node="end",
                condition=EdgeCondition(field="state.score", operator="gte", value=0.8),
                priority=10,
            ),
            Edge(from_node="b", to_node="a", priority=0),  # fallback loop
        ],
    )
    compiled = compiler.compile(g)
    assert compiled is not None


# ---------------------------------------------------------------------------
# SPEC §4.2 — condition fields must exist in state_schema
# ---------------------------------------------------------------------------

def test_condition_field_not_in_schema_raises():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": end_node()},
        state_schema=[StateFieldDefinition(name="intent", type=StateFieldType.STR)],
        edges=[
            Edge(
                from_node="a",
                to_node="b",
                condition=EdgeCondition(field="state.nonexistent_field", operator="eq", value="X"),
            )
        ],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "nonexistent_field" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SPEC §4.3 — condition value type must be compatible
# ---------------------------------------------------------------------------

def test_condition_type_mismatch_raises():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": end_node()},
        state_schema=[StateFieldDefinition(name="score", type=StateFieldType.FLOAT)],
        edges=[
            Edge(
                from_node="a",
                to_node="b",
                condition=EdgeCondition(field="state.score", operator="gte", value="not_a_number"),
            )
        ],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "not compatible" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SPEC §4.1 — condition field must start with "state."
# ---------------------------------------------------------------------------

def test_condition_field_without_state_prefix_raises():
    g = GraphDefinition(
        id="test",
        entry_node="a",
        nodes={"a": make_node(), "b": end_node()},
        state_schema=[StateFieldDefinition(name="intent", type=StateFieldType.STR)],
        edges=[
            Edge(
                from_node="a",
                to_node="b",
                condition=EdgeCondition(field="intent", operator="eq", value="HOT"),
            )
        ],
    )
    with pytest.raises(GraphCompilationError) as exc_info:
        compiler.compile(g)
    assert "state." in str(exc_info.value)
