"""
Tests for A2 (node timeout), A3 (SET_STATE), B1 (TRANSFORM),
B2 (START), B5 (explicit execution_id).

Uses mock tools to avoid real LLM calls.
"""
import asyncio
import pytest

from agentflow.core.compiler import GraphCompiler, GraphCompilationError
from agentflow.core.models import (
    Edge,
    EdgeCondition,
    ExecutionStatus,
    GraphDefinition,
    NodeDefinition,
    NodeType,
    StateFieldDefinition,
    StateFieldType,
)
from agentflow.core.runtime import ExecutionRuntime
from agentflow.core.state_manager import InMemoryStateManager, StateManagerError


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


async def _run(
    graph_def: GraphDefinition,
    initial_input: dict,
    execution_id: str | None = None,
) -> tuple[ExecutionStatus, dict, str]:
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(graph_def, initial_input, execution_id=execution_id)
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)
    return final.status, final.graph_state, final.execution_id


# ---------------------------------------------------------------------------
# A2 — Per-node timeout (SPEC §5.4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_node_timeout_fails_node():
    """A node that exceeds timeout_seconds should fail."""
    from agentflow.executors.tool_executor import register_tool

    async def slow(**kwargs) -> dict:
        await asyncio.sleep(10)
        return {}

    register_tool("slow_node_tool", slow)

    g = GraphDefinition(
        id="node_timeout_test",
        entry_node="slow",
        nodes={
            "slow": NodeDefinition(
                type=NodeType.TOOL,
                config={
                    "tool_name": "slow_node_tool",
                    "output_mapping": {},
                    "max_retries": 0,
                    "timeout_seconds": 0.01,  # 10ms
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="slow", to_node="end")],
    )
    status, _, _ = await _run(g, {})
    assert status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_node_timeout_routes_to_on_error():
    """A timed-out node with on_error should route to the error node."""
    from agentflow.executors.tool_executor import register_tool

    async def slow(**kwargs) -> dict:
        await asyncio.sleep(10)
        return {}

    async def error_handler(**kwargs) -> dict:
        return {"handled": True}

    register_tool("slow_with_on_error", slow)
    register_tool("error_handler_tool", error_handler)

    g = GraphDefinition(
        id="node_timeout_on_error",
        entry_node="slow",
        state_schema=[
            StateFieldDefinition(name="handled", type=StateFieldType.BOOL),
        ],
        nodes={
            "slow": NodeDefinition(
                type=NodeType.TOOL,
                config={
                    "tool_name": "slow_with_on_error",
                    "output_mapping": {},
                    "max_retries": 0,
                    "timeout_seconds": 0.01,
                },
                on_error="error_node",
            ),
            "error_node": tool_node(
                "error_handler_tool", output_mapping={"handled": "handled"}
            ),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="slow", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )
    status, state, _ = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state.get("handled") is True


# ---------------------------------------------------------------------------
# A3 — SET_STATE node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_state_writes_literal_values():
    """SET_STATE should write literal values to the state."""
    g = GraphDefinition(
        id="set_state_literal",
        entry_node="init",
        state_schema=[
            StateFieldDefinition(name="retry_count", type=StateFieldType.INT),
            StateFieldDefinition(name="status", type=StateFieldType.STR),
        ],
        nodes={
            "init": NodeDefinition(
                type=NodeType.SET_STATE,
                config={
                    "assignments": {
                        "retry_count": 0,
                        "status": "pending",
                    }
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="init", to_node="end")],
    )
    status, state, _ = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state["retry_count"] == 0
    assert state["status"] == "pending"


@pytest.mark.asyncio
async def test_set_state_copies_state_reference():
    """SET_STATE with state.field should copy the current value."""
    g = GraphDefinition(
        id="set_state_ref",
        entry_node="copy",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
            StateFieldDefinition(name="previous_intent", type=StateFieldType.STR),
        ],
        nodes={
            "copy": NodeDefinition(
                type=NodeType.SET_STATE,
                config={
                    "assignments": {
                        "previous_intent": "state.intent",
                    }
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="copy", to_node="end")],
    )
    status, state, _ = await _run(g, {"intent": "HOT"})
    assert status == ExecutionStatus.COMPLETED
    assert state["previous_intent"] == "HOT"


def test_set_state_compiler_rejects_unknown_field():
    """Compiler should reject SET_STATE that writes to undeclared state fields."""
    g = GraphDefinition(
        id="set_state_invalid",
        entry_node="init",
        state_schema=[
            StateFieldDefinition(name="known_field", type=StateFieldType.STR),
        ],
        nodes={
            "init": NodeDefinition(
                type=NodeType.SET_STATE,
                config={"assignments": {"unknown_field": "value"}},
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="init", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


# ---------------------------------------------------------------------------
# B1 — TRANSFORM node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transform_set_from_field():
    """TRANSFORM: set operation with from_field copies a state value."""
    g = GraphDefinition(
        id="transform_set",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="source", type=StateFieldType.STR),
            StateFieldDefinition(name="dest", type=StateFieldType.STR),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {"set": "dest", "from_field": "state.source"},
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    status, state, _ = await _run(g, {"source": "hello"})
    assert status == ExecutionStatus.COMPLETED
    assert state["dest"] == "hello"


@pytest.mark.asyncio
async def test_transform_template():
    """TRANSFORM: template operation interpolates state fields."""
    g = GraphDefinition(
        id="transform_template",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="first_name", type=StateFieldType.STR),
            StateFieldDefinition(name="last_name", type=StateFieldType.STR),
            StateFieldDefinition(name="full_name", type=StateFieldType.STR),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {
                            "set": "full_name",
                            "template": "{state.first_name} {state.last_name}",
                        },
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    status, state, _ = await _run(g, {"first_name": "John", "last_name": "Doe"})
    assert status == ExecutionStatus.COMPLETED
    assert state["full_name"] == "John Doe"


@pytest.mark.asyncio
async def test_transform_cast():
    """TRANSFORM: cast operation converts types."""
    g = GraphDefinition(
        id="transform_cast",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="count_str", type=StateFieldType.STR),
            StateFieldDefinition(name="count_int", type=StateFieldType.INT),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {
                            "set": "count_int",
                            "from_field": "state.count_str",
                            "cast": "int",
                        },
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    status, state, _ = await _run(g, {"count_str": "42"})
    assert status == ExecutionStatus.COMPLETED
    assert state["count_int"] == 42


def test_transform_compiler_rejects_unknown_dest_field():
    """Compiler should reject TRANSFORM that writes to undeclared state fields."""
    g = GraphDefinition(
        id="transform_invalid",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="known", type=StateFieldType.STR),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {"set": "unknown_field", "from_field": "state.known"},
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


# ---------------------------------------------------------------------------
# B2 — START node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_node_injects_input_as_text():
    """START node with as_text=true should inject input_as_text."""
    g = GraphDefinition(
        id="start_node_test",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="user_message", type=StateFieldType.STR),
            StateFieldDefinition(name="input_as_text", type=StateFieldType.STR),
        ],
        nodes={
            "start": NodeDefinition(
                type=NodeType.START,
                config={
                    "inputs": [
                        {"name": "user_message", "type": "str", "as_text": True},
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="start", to_node="end")],
    )
    status, state, _ = await _run(g, {"user_message": "hello world"})
    assert status == ExecutionStatus.COMPLETED
    assert state["input_as_text"] == "hello world"


def test_start_node_compiler_rejects_undeclared_input():
    """Compiler should reject START node with input not in state_schema."""
    g = GraphDefinition(
        id="start_invalid",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="other_field", type=StateFieldType.STR),
        ],
        nodes={
            "start": NodeDefinition(
                type=NodeType.START,
                config={
                    "inputs": [
                        {"name": "user_message", "type": "str"},
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="start", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


def test_start_node_compiler_rejects_missing_input_as_text_field():
    """Compiler should reject START with as_text=true but no input_as_text in schema."""
    g = GraphDefinition(
        id="start_no_as_text_field",
        entry_node="start",
        state_schema=[
            StateFieldDefinition(name="user_message", type=StateFieldType.STR),
            # input_as_text is NOT declared
        ],
        nodes={
            "start": NodeDefinition(
                type=NodeType.START,
                config={
                    "inputs": [
                        {"name": "user_message", "type": "str", "as_text": True},
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="start", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


# ---------------------------------------------------------------------------
# B3 — ExecutionTrace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execution_trace_contains_node_records():
    """get_trace should return all node records for the execution."""
    from agentflow.executors.tool_executor import register_tool

    async def noop_trace(**kwargs) -> dict:
        return {}

    register_tool("noop_trace", noop_trace)

    g = GraphDefinition(
        id="trace_test",
        entry_node="n1",
        nodes={
            "n1": tool_node("noop_trace"),
            "n2": tool_node("noop_trace"),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="n1", to_node="n2"),
            Edge(from_node="n2", to_node="end"),
        ],
    )

    compiler = GraphCompiler()
    compiled = compiler.compile(g)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(g, {})
    runtime = ExecutionRuntime(sm)
    await runtime.run(compiled, exec_state)

    trace = await sm.get_trace(exec_state.execution_id)
    node_ids = [r.node_id for r in trace.node_records]
    assert "n1" in node_ids
    assert "n2" in node_ids
    assert trace.status == ExecutionStatus.COMPLETED
    assert trace.duration_ms >= 0


# ---------------------------------------------------------------------------
# B5 — Explicit execution_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_execution_id_is_used():
    """Creating execution with explicit ID should use that ID."""
    g = GraphDefinition(
        id="explicit_id_test",
        entry_node="end",
        nodes={"end": end_node()},
        edges=[],
    )
    _, _, exec_id = await _run(g, {}, execution_id="my-custom-id-001")
    assert exec_id == "my-custom-id-001"


@pytest.mark.asyncio
async def test_duplicate_execution_id_raises_error():
    """Creating execution with an already-used ID should raise StateManagerError."""
    g = GraphDefinition(
        id="dup_id_test",
        entry_node="end",
        nodes={"end": end_node()},
        edges=[],
    )
    sm = InMemoryStateManager()
    await sm.create_execution(g, {}, execution_id="duplicate-id")
    with pytest.raises(StateManagerError, match="already exists"):
        await sm.create_execution(g, {}, execution_id="duplicate-id")


# ---------------------------------------------------------------------------
# B1 — TRANSFORM: extract operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transform_extract_dict_field():
    """TRANSFORM: extract pulls a nested key from a dict value in state."""
    g = GraphDefinition(
        id="transform_extract",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="payload", type=StateFieldType.STR),
            StateFieldDefinition(name="city", type=StateFieldType.STR),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {
                            "set": "city",
                            "from_field": "state.payload",
                            "extract": "address.city",
                        },
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    # payload is a dict stored in state — we pass it as a dict directly
    initial = {"payload": {"address": {"city": "Madrid"}}}
    status, state, _ = await _run(g, initial)
    assert status == ExecutionStatus.COMPLETED
    assert state["city"] == "Madrid"


@pytest.mark.asyncio
async def test_transform_extract_list_index():
    """TRANSFORM: extract supports numeric index into lists."""
    g = GraphDefinition(
        id="transform_extract_list",
        entry_node="t",
        state_schema=[
            StateFieldDefinition(name="items", type=StateFieldType.STR),
            StateFieldDefinition(name="first_item", type=StateFieldType.STR),
        ],
        nodes={
            "t": NodeDefinition(
                type=NodeType.TRANSFORM,
                config={
                    "operations": [
                        {
                            "set": "first_item",
                            "from_field": "state.items",
                            "extract": "0",
                        },
                    ]
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="t", to_node="end")],
    )
    initial = {"items": ["alpha", "beta", "gamma"]}
    status, state, _ = await _run(g, initial)
    assert status == ExecutionStatus.COMPLETED
    assert state["first_item"] == "alpha"


# ---------------------------------------------------------------------------
# B4 — AgentNode output_schema_inline (unit: _build_inline_schema)
# ---------------------------------------------------------------------------

def test_build_inline_schema_basic_types():
    """_build_inline_schema builds a Pydantic model with correct field types."""
    from agentflow.executors.agent_executor import _build_inline_schema

    spec = {
        "intent": {"type": "str"},
        "confidence": {"type": "float"},
        "count": {"type": "int"},
        "active": {"type": "bool"},
    }
    Model = _build_inline_schema(spec, model_name="TestSchema")

    instance = Model(intent="HOT", confidence=0.9, count=5, active=True)
    assert instance.intent == "HOT"
    assert instance.confidence == 0.9
    assert instance.count == 5
    assert instance.active is True


def test_build_inline_schema_enum_field():
    """_build_inline_schema with enum creates a Literal-constrained field."""
    from pydantic import ValidationError
    from agentflow.executors.agent_executor import _build_inline_schema

    spec = {
        "intent": {"type": "str", "enum": ["HOT", "WARM", "COLD"]},
    }
    Model = _build_inline_schema(spec, model_name="EnumSchema")

    instance = Model(intent="HOT")
    assert instance.intent == "HOT"

    with pytest.raises(ValidationError):
        Model(intent="INVALID_VALUE")


def test_build_inline_schema_with_default():
    """_build_inline_schema with default makes the field optional."""
    from agentflow.executors.agent_executor import _build_inline_schema

    spec = {
        "response": {"type": "str"},
        "score": {"type": "float", "default": 0.0},
    }
    Model = _build_inline_schema(spec, model_name="DefaultSchema")

    # score has a default — omitting it should work
    instance = Model(response="hello")
    assert instance.score == 0.0


def test_compiler_rejects_inline_schema_combined_with_output_schema():
    """Compiler must reject AgentNode that defines both output_schema and output_schema_inline."""
    g = GraphDefinition(
        id="agent_dual_schema",
        entry_node="a",
        nodes={
            "a": NodeDefinition(
                type=NodeType.AGENT,
                config={
                    "output_schema": "some.module.SomeClass",
                    "output_schema_inline": {"field": {"type": "str"}},
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="a", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)
