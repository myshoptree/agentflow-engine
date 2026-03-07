"""
Tests for ExecutionRuntime — validates SPEC §1, §2.4, §2.5, §3, §5, §6.
Uses mock executors to avoid real LLM calls.
"""
import asyncio
import pytest

from agentflow.core.compiler import GraphCompiler
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
from agentflow.core.state_manager import InMemoryStateManager
from agentflow.executors import registry as executor_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def agent_node(state_output_mapping: dict = {}) -> NodeDefinition:
    return NodeDefinition(
        type=NodeType.AGENT,
        config={"state_output_mapping": state_output_mapping, "max_retries": 0},
    )


def tool_node(tool_name: str, output_mapping: dict = {}) -> NodeDefinition:
    return NodeDefinition(
        type=NodeType.TOOL,
        config={"tool_name": tool_name, "output_mapping": output_mapping, "max_retries": 0},
    )


def end_node() -> NodeDefinition:
    return NodeDefinition(type=NodeType.END, config={})


async def _run(graph_def: GraphDefinition, initial_input: dict) -> tuple[ExecutionStatus, dict]:
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(graph_def, initial_input)
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)
    return final.status, final.graph_state


# ---------------------------------------------------------------------------
# SPEC §1.3 — simple graph reaches COMPLETED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simple_graph_completes():
    from agentflow.executors.tool_executor import register_tool

    async def noop_tool() -> dict:
        return {}

    register_tool("noop", noop_tool)

    g = GraphDefinition(
        id="simple",
        entry_node="step1",
        nodes={
            "step1": tool_node("noop"),
            "end": end_node(),
        },
        edges=[Edge(from_node="step1", to_node="end")],
    )
    status, _ = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED


# ---------------------------------------------------------------------------
# Router Pattern — SPEC §2.5 conditional routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_routes_to_correct_branch():
    from agentflow.executors.tool_executor import register_tool

    async def classifier(**kwargs) -> dict:
        return {"intent": "HOT"}

    async def closer_tool(**kwargs) -> dict:
        return {"result": "closed"}

    async def educator_tool(**kwargs) -> dict:
        return {"result": "educated"}

    register_tool("classifier_tool", classifier)
    register_tool("closer_tool", closer_tool)
    register_tool("educator_tool", educator_tool)

    g = GraphDefinition(
        id="router",
        entry_node="classifier",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
            StateFieldDefinition(name="result", type=StateFieldType.STR),
        ],
        nodes={
            "classifier": tool_node("classifier_tool", output_mapping={"intent": "intent"}),
            "closer": tool_node("closer_tool", output_mapping={"result": "result"}),
            "educator": tool_node("educator_tool", output_mapping={"result": "result"}),
            "end": end_node(),
        },
        edges=[
            Edge(
                from_node="classifier",
                to_node="closer",
                condition=EdgeCondition(field="state.intent", operator="eq", value="HOT"),
                priority=10,
            ),
            Edge(from_node="classifier", to_node="educator", priority=0),
            Edge(from_node="closer", to_node="end"),
            Edge(from_node="educator", to_node="end"),
        ],
    )

    status, state = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state["intent"] == "HOT"
    assert state["result"] == "closed"   # went to closer, not educator


@pytest.mark.asyncio
async def test_router_falls_back_when_no_condition_matches():
    from agentflow.executors.tool_executor import register_tool

    async def classifier_cold(**kwargs) -> dict:
        return {"intent": "COLD"}

    register_tool("classifier_cold", classifier_cold)

    g = GraphDefinition(
        id="router_fallback",
        entry_node="classifier",
        state_schema=[
            StateFieldDefinition(name="intent", type=StateFieldType.STR),
            StateFieldDefinition(name="result", type=StateFieldType.STR),
        ],
        nodes={
            "classifier": tool_node("classifier_cold", output_mapping={"intent": "intent"}),
            "closer": tool_node("classifier_cold", output_mapping={"intent": "intent"}),
            "educator": NodeDefinition(
                type=NodeType.TOOL,
                config={"tool_name": "noop_str", "output_mapping": {"result": "msg"}, "max_retries": 0},
            ),
            "end": end_node(),
        },
        edges=[
            Edge(
                from_node="classifier",
                to_node="closer",
                condition=EdgeCondition(field="state.intent", operator="eq", value="HOT"),
                priority=10,
            ),
            Edge(from_node="classifier", to_node="educator", priority=0),  # fallback
            Edge(from_node="educator", to_node="end"),
            Edge(from_node="closer", to_node="end"),
        ],
    )

    async def noop_str(**kwargs) -> dict:
        return {"msg": "fallback_reached"}

    register_tool("noop_str", noop_str)

    status, state = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state["result"] == "fallback_reached"


# ---------------------------------------------------------------------------
# SPEC §2.4 — max_depth stops infinite loops
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_depth_stops_loop():
    from agentflow.executors.tool_executor import register_tool

    async def always_low(**kwargs) -> dict:
        return {"score": 0.1}

    register_tool("always_low", always_low)

    g = GraphDefinition(
        id="loop_guard",
        entry_node="producer",
        max_depth=5,
        state_schema=[StateFieldDefinition(name="score", type=StateFieldType.FLOAT)],
        nodes={
            "producer": tool_node("always_low", output_mapping={"score": "score"}),
            "end": end_node(),
        },
        edges=[
            Edge(
                from_node="producer",
                to_node="end",
                condition=EdgeCondition(field="state.score", operator="gte", value=0.8),
                priority=10,
            ),
            Edge(from_node="producer", to_node="producer", priority=0),  # loop
        ],
    )
    status, _ = await _run(g, {})
    assert status == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# SPEC §3.2 — checkpoints are created per node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoints_created_per_node():
    from agentflow.executors.tool_executor import register_tool

    async def step(**kwargs) -> dict:
        return {}

    register_tool("step_tool", step)

    g = GraphDefinition(
        id="checkpoints",
        entry_node="n1",
        nodes={
            "n1": tool_node("step_tool"),
            "n2": tool_node("step_tool"),
            "n3": end_node(),
        },
        edges=[
            Edge(from_node="n1", to_node="n2"),
            Edge(from_node="n2", to_node="n3"),
        ],
    )

    compiler_ = GraphCompiler()
    compiled = compiler_.compile(g)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(g, {})
    runtime = ExecutionRuntime(sm)
    await runtime.run(compiled, exec_state)

    checkpoints = await sm.get_checkpoints(exec_state.execution_id)
    # n1, n2, and end node all checkpoint → 3
    assert len(checkpoints) == 3
    node_ids = [c.node_id for c in checkpoints]
    assert "n1" in node_ids
    assert "n2" in node_ids
    assert "n3" in node_ids


# ---------------------------------------------------------------------------
# SPEC §5.3 — global timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_global_timeout():
    from agentflow.executors.tool_executor import register_tool

    async def slow_tool(**kwargs) -> dict:
        await asyncio.sleep(10)  # much longer than timeout
        return {}

    register_tool("slow_tool", slow_tool)

    g = GraphDefinition(
        id="timeout_test",
        entry_node="slow",
        global_timeout_seconds=0.05,  # 50ms
        nodes={
            "slow": tool_node("slow_tool"),
            "end": end_node(),
        },
        edges=[Edge(from_node="slow", to_node="end")],
    )
    status, _ = await _run(g, {})
    assert status == ExecutionStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# SPEC §6.1 — HumanInputNode suspends execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_human_input_suspends():
    g = GraphDefinition(
        id="human_input",
        entry_node="wait_human",
        nodes={
            "wait_human": NodeDefinition(type=NodeType.HUMAN_INPUT, config={}),
            "end": end_node(),
        },
        edges=[Edge(from_node="wait_human", to_node="end")],
    )
    status, _ = await _run(g, {})
    assert status == ExecutionStatus.SUSPENDED


# ---------------------------------------------------------------------------
# SPEC §6.2 — resume from SUSPENDED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_suspended_execution():
    from agentflow.executors.tool_executor import register_tool

    async def echo_tool(**kwargs) -> dict:
        return {}

    register_tool("echo_tool", echo_tool)

    g = GraphDefinition(
        id="resume_test",
        entry_node="wait_human",
        nodes={
            "wait_human": NodeDefinition(type=NodeType.HUMAN_INPUT, config={}),
            "after": tool_node("echo_tool"),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="wait_human", to_node="after"),
            Edge(from_node="after", to_node="end"),
        ],
    )

    compiler_ = GraphCompiler()
    compiled = compiler_.compile(g)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(g, {})
    runtime = ExecutionRuntime(sm)

    # First run → SUSPENDED
    result = await runtime.run(compiled, exec_state)
    assert result.status == ExecutionStatus.SUSPENDED

    # Resume
    resumed_state = await sm.resume(result.execution_id, {"human_input": "approved"})
    final = await runtime.run(compiled, resumed_state)
    assert final.status == ExecutionStatus.COMPLETED
