"""
Tests for PARALLEL node executor.

Covers:
- Happy path: concurrent branches merge results
- Branch failure: one branch fails → ParallelExecutorError → execution FAILED
- Merge strategy: later branch overrides earlier on key conflict
- Compiler: rejects branch node_id not in graph
"""
from __future__ import annotations

import asyncio
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
from agentflow.executors.tool_executor import register_tool


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


def parallel_node(branches: list[str]) -> NodeDefinition:
    return NodeDefinition(
        type=NodeType.PARALLEL,
        config={"branches": branches},
    )


async def _run(graph_def: GraphDefinition, initial_input: dict) -> tuple[ExecutionStatus, dict]:
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(graph_def, initial_input)
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)
    return final.status, final.graph_state


# ---------------------------------------------------------------------------
# Happy path: two branches run concurrently and merge results
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_branches_merge_results():
    """Both parallel branches execute and their outputs are merged into state."""

    async def branch_a(**kwargs) -> dict:
        return {"result_a": "from_a"}

    async def branch_b(**kwargs) -> dict:
        return {"result_b": "from_b"}

    register_tool("parallel_branch_a", branch_a)
    register_tool("parallel_branch_b", branch_b)

    g = GraphDefinition(
        id="parallel_merge",
        entry_node="fan_out",
        state_schema=[
            StateFieldDefinition(name="result_a", type=StateFieldType.STR),
            StateFieldDefinition(name="result_b", type=StateFieldType.STR),
        ],
        nodes={
            "fan_out": parallel_node(["branch_a", "branch_b"]),
            "branch_a": tool_node("parallel_branch_a", output_mapping={"result_a": "result_a"}),
            "branch_b": tool_node("parallel_branch_b", output_mapping={"result_b": "result_b"}),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )

    status, state = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state["result_a"] == "from_a"
    assert state["result_b"] == "from_b"


# ---------------------------------------------------------------------------
# Concurrent execution: branches run in parallel (timing check)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_branches_run_concurrently():
    """Two branches each sleep 0.1s — total should be ~0.1s, not ~0.2s."""
    import time

    async def slow_a(**kwargs) -> dict:
        await asyncio.sleep(0.1)
        return {"a": "done"}

    async def slow_b(**kwargs) -> dict:
        await asyncio.sleep(0.1)
        return {"b": "done"}

    register_tool("concurrent_a", slow_a)
    register_tool("concurrent_b", slow_b)

    g = GraphDefinition(
        id="parallel_concurrent",
        entry_node="fan_out",
        state_schema=[
            StateFieldDefinition(name="a", type=StateFieldType.STR),
            StateFieldDefinition(name="b", type=StateFieldType.STR),
        ],
        nodes={
            "fan_out": parallel_node(["c_branch_a", "c_branch_b"]),
            "c_branch_a": tool_node("concurrent_a", output_mapping={"a": "a"}),
            "c_branch_b": tool_node("concurrent_b", output_mapping={"b": "b"}),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )

    t0 = time.monotonic()
    status, state = await _run(g, {})
    elapsed = time.monotonic() - t0

    assert status == ExecutionStatus.COMPLETED
    # If sequential: ~0.2s. If parallel: ~0.1s. Threshold at 0.18s.
    assert elapsed < 0.18, f"Branches did not run concurrently (elapsed={elapsed:.3f}s)"


# ---------------------------------------------------------------------------
# Branch failure: one branch raises → execution FAILED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_branch_failure_fails_execution():
    """If a parallel branch fails, the whole execution ends in FAILED."""

    async def ok_branch(**kwargs) -> dict:
        return {"ok": "yes"}

    async def failing_branch(**kwargs) -> dict:
        raise RuntimeError("branch exploded")

    register_tool("ok_branch_tool", ok_branch)
    register_tool("failing_branch_tool", failing_branch)

    g = GraphDefinition(
        id="parallel_failure",
        entry_node="fan_out",
        state_schema=[
            StateFieldDefinition(name="ok", type=StateFieldType.STR),
        ],
        nodes={
            "fan_out": parallel_node(["ok_br", "fail_br"]),
            "ok_br": tool_node("ok_branch_tool", output_mapping={"ok": "ok"}),
            "fail_br": tool_node("failing_branch_tool"),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )

    status, _ = await _run(g, {})
    assert status == ExecutionStatus.FAILED


# ---------------------------------------------------------------------------
# Merge conflict: later branch key overrides earlier branch key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_merge_later_branch_wins():
    """When two branches write the same key, the last one in the list wins."""

    async def first_writer(**kwargs) -> dict:
        return {"shared": "from_first"}

    async def second_writer(**kwargs) -> dict:
        return {"shared": "from_second"}

    register_tool("first_writer_tool", first_writer)
    register_tool("second_writer_tool", second_writer)

    g = GraphDefinition(
        id="parallel_conflict",
        entry_node="fan_out",
        state_schema=[
            StateFieldDefinition(name="shared", type=StateFieldType.STR),
        ],
        nodes={
            "fan_out": parallel_node(["writer_1", "writer_2"]),
            "writer_1": tool_node("first_writer_tool", output_mapping={"shared": "shared"}),
            "writer_2": tool_node("second_writer_tool", output_mapping={"shared": "shared"}),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )

    status, state = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    # "writer_2" is second in branches list → its value wins
    assert state["shared"] == "from_second"


# ---------------------------------------------------------------------------
# Single branch: works correctly with one branch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_single_branch():
    """Parallel node with a single branch still works correctly."""

    async def single(**kwargs) -> dict:
        return {"value": "single_result"}

    register_tool("single_branch_tool", single)

    g = GraphDefinition(
        id="parallel_single",
        entry_node="fan_out",
        state_schema=[
            StateFieldDefinition(name="value", type=StateFieldType.STR),
        ],
        nodes={
            "fan_out": parallel_node(["solo_branch"]),
            "solo_branch": tool_node("single_branch_tool", output_mapping={"value": "value"}),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )

    status, state = await _run(g, {})
    assert status == ExecutionStatus.COMPLETED
    assert state["value"] == "single_result"


# ---------------------------------------------------------------------------
# Compiler: branch node_id not in graph → error
# ---------------------------------------------------------------------------

def test_parallel_compiler_rejects_missing_branch_node():
    """Compiler should reject parallel node referencing a branch node not in the graph."""
    g = GraphDefinition(
        id="parallel_invalid_branch",
        entry_node="fan_out",
        nodes={
            "fan_out": parallel_node(["branch_exists", "branch_missing"]),
            "branch_exists": tool_node("some_tool"),
            "end": end_node(),
        },
        edges=[Edge(from_node="fan_out", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)
