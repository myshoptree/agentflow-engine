"""
Tests for RetryPolicy and configurable backoff in the runtime.
"""
import asyncio
import pytest

from agentflow.core.models import RetryPolicy
from agentflow.core.runtime import _calc_backoff, _get_retry_policy
from agentflow.core.models import NodeDefinition, NodeType


def make_node(config: dict) -> NodeDefinition:
    return NodeDefinition(type=NodeType.TOOL, config=config)


# ---------------------------------------------------------------------------
# RetryPolicy model
# ---------------------------------------------------------------------------

def test_default_retry_policy():
    p = RetryPolicy()
    assert p.max_retries == 3
    assert p.backoff_base == 1.0
    assert p.backoff_max == 30.0
    assert p.backoff_multiplier == 2.0


def test_backoff_formula():
    p = RetryPolicy(backoff_base=1.0, backoff_multiplier=2.0, backoff_max=30.0)
    assert _calc_backoff(p, 1) == 1.0   # 1 * 2^0
    assert _calc_backoff(p, 2) == 2.0   # 1 * 2^1
    assert _calc_backoff(p, 3) == 4.0   # 1 * 2^2
    assert _calc_backoff(p, 4) == 8.0


def test_backoff_capped_at_max():
    p = RetryPolicy(backoff_base=10.0, backoff_multiplier=3.0, backoff_max=15.0)
    assert _calc_backoff(p, 3) == 15.0  # 10 * 9 = 90 → capped at 15


def test_custom_backoff_base():
    p = RetryPolicy(backoff_base=0.1, backoff_multiplier=2.0, backoff_max=5.0)
    assert _calc_backoff(p, 1) == pytest.approx(0.1)
    assert _calc_backoff(p, 2) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# _get_retry_policy extraction
# ---------------------------------------------------------------------------

def test_get_retry_policy_from_explicit():
    node = make_node({"tool_name": "x", "retry_policy": {"max_retries": 5, "backoff_base": 2.0}})
    p = _get_retry_policy(node)
    assert p.max_retries == 5
    assert p.backoff_base == 2.0


def test_get_retry_policy_legacy_max_retries():
    node = make_node({"tool_name": "x", "max_retries": 2})
    p = _get_retry_policy(node)
    assert p.max_retries == 2
    assert p.backoff_base == 1.0  # default


def test_get_retry_policy_default_when_no_config():
    node = make_node({"tool_name": "x"})
    p = _get_retry_policy(node)
    assert p.max_retries == 0   # tool nodes default to 0 if not specified


def test_retry_policy_overrides_max_retries():
    """When both retry_policy and max_retries are in config, retry_policy wins."""
    node = make_node({
        "tool_name": "x",
        "max_retries": 1,
        "retry_policy": {"max_retries": 7, "backoff_base": 0.5},
    })
    p = _get_retry_policy(node)
    assert p.max_retries == 7
    assert p.backoff_base == 0.5


# ---------------------------------------------------------------------------
# Runtime integration: retry actually fires N times
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runtime_retries_correct_number_of_times():
    from agentflow.core.compiler import GraphCompiler
    from agentflow.core.models import (
        Edge, GraphDefinition, NodeDefinition, NodeType,
    )
    from agentflow.core.runtime import ExecutionRuntime
    from agentflow.core.state_manager import InMemoryStateManager
    from agentflow.executors.tool_executor import register_tool

    call_count = 0

    async def flaky_tool(**kwargs) -> dict:
        nonlocal call_count
        call_count += 1
        raise ValueError(f"fail #{call_count}")

    register_tool("flaky", flaky_tool)

    g = GraphDefinition(
        id="retry-test",
        entry_node="flaky_node",
        nodes={
            "flaky_node": NodeDefinition(
                type=NodeType.TOOL,
                config={
                    "tool_name": "flaky",
                    "retry_policy": {"max_retries": 2, "backoff_base": 0.01, "backoff_max": 0.01},
                },
            ),
            "end": NodeDefinition(type=NodeType.END, config={}),
        },
        edges=[Edge(from_node="flaky_node", to_node="end")],
    )

    compiler = GraphCompiler()
    compiled = compiler.compile(g)
    sm = InMemoryStateManager()
    state = await sm.create_execution(g, {})
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, state)

    from agentflow.core.models import ExecutionStatus
    assert final.status == ExecutionStatus.FAILED
    # max_retries=2 → 1 initial + 2 retries = 3 total calls
    assert call_count == 3
