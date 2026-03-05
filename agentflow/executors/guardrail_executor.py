"""
Guardrail Executor — evaluates state fields against safety checks.

Implements C1 from the implementation plan.
SPEC references:
- §11.1–§11.4: Guardrail node contract

Supported check types: pii, toxicity, custom_llm.
Provider-agnostic — presidio for PII, LLM-as-judge for custom_llm.
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import (
    ExecutionState,
    GuardrailCheck,
    GuardrailCheckType,
    GuardrailNodeConfig,
    NodeDefinition,
)


class GuardrailExecutorError(Exception):
    pass


def _get_field_value(field: str, graph_state: dict[str, Any]) -> Any:
    """Resolve a state field reference."""
    if field.startswith("state."):
        key = field[len("state."):]
    else:
        key = field
    return graph_state.get(key)


async def _check_pii(value: Any) -> tuple[bool, str]:
    """
    Detect PII using presidio-analyzer.
    Returns (failed, reason). Failed=True means PII was found.
    """
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import]
    except ImportError:
        raise GuardrailExecutorError(
            "PII check requires 'presidio-analyzer'. "
            "Install it with: uv add presidio-analyzer"
        )

    analyzer = AnalyzerEngine()
    text = str(value) if value is not None else ""
    results = analyzer.analyze(text=text, language="en")

    if results:
        entities = [r.entity_type for r in results]
        return True, f"PII detected: {', '.join(set(entities))}"
    return False, "no PII detected"


async def _check_toxicity(value: Any) -> tuple[bool, str]:
    """
    Heuristic toxicity check. For production, replace with a proper model.
    Returns (failed, reason).
    """
    # Basic heuristic — in production replace with a real classifier
    TOXIC_PATTERNS = [
        "kill", "hate", "violent", "abuse", "explicit",
    ]
    text = str(value).lower() if value is not None else ""
    for pattern in TOXIC_PATTERNS:
        if pattern in text:
            return True, f"Potential toxicity detected (keyword: '{pattern}')"
    return False, "no toxicity detected"


async def _check_custom_llm(check: GuardrailCheck, value: Any) -> tuple[bool, str]:
    """
    LLM-as-judge check. The prompt should ask a yes/no question.
    'yes' means the check fails (content is problematic).
    """
    from pydantic_ai import Agent  # type: ignore[import]

    model = check.model or "anthropic:claude-sonnet-4-6"
    prompt = check.prompt or "Does this content violate safety policies? Answer yes or no."

    agent: Agent = Agent(
        model=model,
        system_prompt=prompt,
        output_type=str,
    )
    result = await agent.run(str(value))
    answer = result.output.strip().lower()
    failed = answer.startswith("yes")
    return failed, f"LLM judge answered: {answer}"


class GuardrailExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        """
        Run all checks in order. Returns state updates with result/reason.
        The runtime uses result='fail' to route to on_fail (SPEC §11.3).
        """
        config = GuardrailNodeConfig(**node_def.config)

        for check in config.checks:
            field_value = _get_field_value(check.field, execution_state.graph_state)

            if check.type == GuardrailCheckType.PII:
                failed, reason = await _check_pii(field_value)
            elif check.type == GuardrailCheckType.TOXICITY:
                failed, reason = await _check_toxicity(field_value)
            elif check.type == GuardrailCheckType.CUSTOM_LLM:
                failed, reason = await _check_custom_llm(check, field_value)
            else:
                raise GuardrailExecutorError(f"Unknown check type: '{check.type}'")

            if failed:
                base_updates: dict[str, Any] = {"result": "fail", "reason": reason}
                # Apply output_mapping to state fields
                updates = _apply_output_mapping(config.output_mapping, base_updates)
                return updates

        base_updates = {"result": "pass", "reason": "all checks passed"}
        return _apply_output_mapping(config.output_mapping, base_updates)


def _apply_output_mapping(
    output_mapping: dict[str, str],
    base: dict[str, Any],
) -> dict[str, Any]:
    """Map internal result/reason to state fields via output_mapping."""
    if not output_mapping:
        return base

    updates: dict[str, Any] = {}
    # Always keep result in output so runtime can check it
    updates["result"] = base.get("result")
    for state_field, source_key in output_mapping.items():
        updates[state_field] = base.get(source_key)
    return updates
