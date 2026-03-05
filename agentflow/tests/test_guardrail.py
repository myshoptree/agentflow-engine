"""
Tests for C1 — GUARDRAIL node.

Uses mocks to avoid real presidio or LLM calls.
SPEC references: §11.1–§11.4
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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


async def _run(graph_def: GraphDefinition, initial_input: dict) -> tuple[ExecutionStatus, dict]:
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)
    sm = InMemoryStateManager()
    exec_state = await sm.create_execution(graph_def, initial_input)
    runtime = ExecutionRuntime(sm)
    final = await runtime.run(compiled, exec_state)
    return final.status, final.graph_state


# ---------------------------------------------------------------------------
# Compiler validation
# ---------------------------------------------------------------------------

def test_guardrail_compiler_rejects_unknown_on_fail():
    """Compiler should reject GUARDRAIL with on_fail pointing to non-existent node."""
    g = GraphDefinition(
        id="guardrail_invalid_on_fail",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="response", type=StateFieldType.STR),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "toxicity", "field": "state.response"}],
                    "on_fail": "nonexistent_node",
                },
            ),
            "end": end_node(),
        },
        edges=[Edge(from_node="guard", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


def test_guardrail_compiler_rejects_unknown_check_field():
    """Compiler should reject GUARDRAIL with check referencing undeclared state field."""
    g = GraphDefinition(
        id="guardrail_unknown_field",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="other_field", type=StateFieldType.STR),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "toxicity", "field": "state.response"}],
                    "on_fail": "error_node",
                },
            ),
            "error_node": end_node(),
            "end": end_node(),
        },
        edges=[Edge(from_node="guard", to_node="end")],
    )
    with pytest.raises(GraphCompilationError):
        GraphCompiler().compile(g)


# ---------------------------------------------------------------------------
# Toxicity check — heuristic (no external dep)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_guardrail_toxicity_pass():
    """Guardrail passes when content is clean."""
    g = GraphDefinition(
        id="guardrail_toxicity_pass",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="response", type=StateFieldType.STR),
            StateFieldDefinition(name="guard_result", type=StateFieldType.STR),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "toxicity", "field": "state.response"}],
                    "on_fail": "error_node",
                    "output_mapping": {"guard_result": "result"},
                },
            ),
            "error_node": end_node(),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
        ],
    )
    status, state = await _run(g, {"response": "The weather is nice today."})
    assert status == ExecutionStatus.COMPLETED
    assert state.get("guard_result") == "pass"


@pytest.mark.asyncio
async def test_guardrail_toxicity_fail_routes_to_on_fail():
    """Guardrail routes to on_fail when toxicity is detected."""
    from agentflow.executors.tool_executor import register_tool

    async def error_handler_g(**kwargs) -> dict:
        return {"error_handled": True}

    register_tool("error_handler_g", error_handler_g)

    g = GraphDefinition(
        id="guardrail_toxicity_fail",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="response", type=StateFieldType.STR),
            StateFieldDefinition(name="error_handled", type=StateFieldType.BOOL),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "toxicity", "field": "state.response"}],
                    "on_fail": "error_node",
                },
            ),
            "error_node": tool_node(
                "error_handler_g", output_mapping={"error_handled": "error_handled"}
            ),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )
    # "violent" triggers the toxicity heuristic
    status, state = await _run(g, {"response": "This content is violent and abusive."})
    assert status == ExecutionStatus.COMPLETED
    assert state.get("error_handled") is True


# ---------------------------------------------------------------------------
# PII check — mocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_guardrail_pii_fail_with_mock():
    """PII check should route to on_fail when PII is detected (mocked)."""
    import agentflow.executors.guardrail_executor as ge_module
    from agentflow.executors.tool_executor import register_tool

    async def pii_error_handler(**kwargs) -> dict:
        return {"pii_blocked": True}

    register_tool("pii_error_handler", pii_error_handler)

    g = GraphDefinition(
        id="guardrail_pii_fail",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="user_email", type=StateFieldType.STR),
            StateFieldDefinition(name="pii_blocked", type=StateFieldType.BOOL),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "pii", "field": "state.user_email"}],
                    "on_fail": "error_node",
                },
            ),
            "error_node": tool_node(
                "pii_error_handler", output_mapping={"pii_blocked": "pii_blocked"}
            ),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )

    original_check_pii = ge_module._check_pii

    async def mock_check_pii(value: object) -> tuple[bool, str]:
        return True, "PII detected: EMAIL_ADDRESS"

    ge_module._check_pii = mock_check_pii  # type: ignore[assignment]
    try:
        status, state = await _run(g, {"user_email": "user@example.com"})
        assert status == ExecutionStatus.COMPLETED
        assert state.get("pii_blocked") is True
    finally:
        ge_module._check_pii = original_check_pii  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Missing presidio — clear error
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# custom_llm check — mocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_guardrail_custom_llm_pass():
    """custom_llm check passes when LLM judge answers 'no'."""
    import agentflow.executors.guardrail_executor as ge_module
    from agentflow.executors.tool_executor import register_tool

    async def handler_llm_pass(**kwargs) -> dict:
        return {}

    register_tool("handler_llm_pass", handler_llm_pass)

    g = GraphDefinition(
        id="guardrail_custom_llm_pass",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="response", type=StateFieldType.STR),
            StateFieldDefinition(name="guard_result", type=StateFieldType.STR),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [
                        {
                            "type": "custom_llm",
                            "field": "state.response",
                            "model": "anthropic:claude-haiku-4-5-20251001",
                            "prompt": "Is this inappropriate? Answer yes or no.",
                        }
                    ],
                    "on_fail": "error_node",
                    "output_mapping": {"guard_result": "result"},
                },
            ),
            "error_node": end_node(),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )

    # Mock _check_custom_llm to return pass (no LLM call)
    original = ge_module._check_custom_llm

    async def mock_custom_llm_pass(check, value):
        return False, "LLM judge answered: no"

    ge_module._check_custom_llm = mock_custom_llm_pass  # type: ignore[assignment]
    try:
        status, state = await _run(g, {"response": "The weather is nice today."})
        assert status == ExecutionStatus.COMPLETED
        assert state.get("guard_result") == "pass"
    finally:
        ge_module._check_custom_llm = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_guardrail_custom_llm_fail_routes_to_on_fail():
    """custom_llm check fails when LLM judge answers 'yes' → routes to on_fail."""
    import agentflow.executors.guardrail_executor as ge_module
    from agentflow.executors.tool_executor import register_tool

    async def llm_fail_handler(**kwargs) -> dict:
        return {"llm_blocked": True}

    register_tool("llm_fail_handler", llm_fail_handler)

    g = GraphDefinition(
        id="guardrail_custom_llm_fail",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="response", type=StateFieldType.STR),
            StateFieldDefinition(name="llm_blocked", type=StateFieldType.BOOL),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [
                        {
                            "type": "custom_llm",
                            "field": "state.response",
                            "model": "anthropic:claude-haiku-4-5-20251001",
                            "prompt": "Is this inappropriate? Answer yes or no.",
                        }
                    ],
                    "on_fail": "error_node",
                },
            ),
            "error_node": tool_node(
                "llm_fail_handler", output_mapping={"llm_blocked": "llm_blocked"}
            ),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )

    original = ge_module._check_custom_llm

    async def mock_custom_llm_fail(check, value):
        return True, "LLM judge answered: yes, this violates policy"

    ge_module._check_custom_llm = mock_custom_llm_fail  # type: ignore[assignment]
    try:
        status, state = await _run(g, {"response": "something problematic"})
        assert status == ExecutionStatus.COMPLETED
        assert state.get("llm_blocked") is True
    finally:
        ge_module._check_custom_llm = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_guardrail_pii_without_presidio_raises_clear_error():
    """PII check without presidio installed should produce a FAILED execution with clear reason."""
    g = GraphDefinition(
        id="guardrail_pii_no_presidio",
        entry_node="guard",
        state_schema=[
            StateFieldDefinition(name="text", type=StateFieldType.STR),
        ],
        nodes={
            "guard": NodeDefinition(
                type=NodeType.GUARDRAIL,
                config={
                    "checks": [{"type": "pii", "field": "state.text"}],
                    "on_fail": "error_node",
                },
            ),
            "error_node": end_node(),
            "end": end_node(),
        },
        edges=[
            Edge(from_node="guard", to_node="end"),
            Edge(from_node="error_node", to_node="end"),
        ],
    )

    import sys
    # Ensure presidio_analyzer is not importable
    original = sys.modules.get("presidio_analyzer")
    sys.modules["presidio_analyzer"] = None  # type: ignore[assignment]
    try:
        status, state = await _run(g, {"text": "some@email.com"})
        # Without presidio, the check raises GuardrailExecutorError
        # which the runtime handles as a node failure → FAILED (no on_error on the node)
        assert status == ExecutionStatus.FAILED
    finally:
        if original is None:
            del sys.modules["presidio_analyzer"]
        else:
            sys.modules["presidio_analyzer"] = original
