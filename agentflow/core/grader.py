"""
Grader — evaluates ExecutionTrace and produces GradeResult.

Implements C2 from the implementation plan.
SPEC references:
- §12.1: Purpose
- §12.2: Grader types (deterministic, llm_judge, heuristic)
- §12.3: Immutability — graders never modify the trace
"""
from __future__ import annotations

from typing import Any

from agentflow.core.models import (
    EdgeCondition,
    ExecutionTrace,
    GradeResult,
    Grader,
    GraderType,
)
from agentflow.dsl.condition_parser import evaluate_condition


class GraderError(Exception):
    pass


class GraderRunner:
    """Evaluates an ExecutionTrace with a given Grader and returns a GradeResult."""

    async def grade(self, trace: ExecutionTrace, grader: Grader) -> GradeResult:
        if grader.type == GraderType.DETERMINISTIC:
            return await self._grade_deterministic(trace, grader)
        if grader.type == GraderType.LLM_JUDGE:
            return await self._grade_llm_judge(trace, grader)
        if grader.type == GraderType.HEURISTIC:
            return await self._grade_heuristic(trace, grader)
        raise GraderError(f"Unknown grader type: '{grader.type}'")

    async def _grade_deterministic(
        self, trace: ExecutionTrace, grader: Grader
    ) -> GradeResult:
        """
        Compare final_state fields against expected values using the DSL (SPEC §12.2).

        Config format:
            {
              "field": "state.intent",
              "operator": "eq",
              "value": "HOT"
            }
        """
        cfg = grader.config
        try:
            condition = EdgeCondition(
                field=cfg["field"],
                operator=cfg["operator"],
                value=cfg.get("value"),
            )
        except Exception as exc:
            raise GraderError(
                f"Grader '{grader.id}' has invalid deterministic config: {exc}"
            ) from exc

        try:
            matched = evaluate_condition(condition, trace.final_state)
        except Exception as exc:
            return GradeResult(
                grader_id=grader.id,
                execution_id=trace.execution_id,
                score=0.0,
                label="error",
                reason=f"Evaluation error: {exc}",
            )

        return GradeResult(
            grader_id=grader.id,
            execution_id=trace.execution_id,
            score=1.0 if matched else 0.0,
            label="pass" if matched else "fail",
            reason=(
                f"Field {cfg['field']} {cfg['operator']} {cfg.get('value')!r}: "
                f"{'matched' if matched else 'did not match'}"
            ),
        )

    async def _grade_llm_judge(
        self, trace: ExecutionTrace, grader: Grader
    ) -> GradeResult:
        """
        Use an LLM to evaluate the trace (SPEC §12.2).

        Config format:
            {
              "model": "anthropic:claude-sonnet-4-6",
              "prompt": "Evaluate if the agent correctly classified the intent...",
              "score_field": "score"   # optional — field in final_state to use as score
            }
        """
        from pydantic import BaseModel
        from pydantic_ai import Agent  # type: ignore[import]

        class JudgeOutput(BaseModel):
            score: float   # 0.0 to 1.0
            reason: str

        model = grader.config.get("model", "anthropic:claude-sonnet-4-6")
        prompt = grader.config.get("prompt", "Evaluate the quality of this agent execution.")

        agent: Agent = Agent(
            model=model,
            system_prompt=prompt,
            output_type=JudgeOutput,
        )

        # Build a summary of the trace for the LLM
        trace_summary = (
            f"Execution ID: {trace.execution_id}\n"
            f"Graph: {trace.graph_id} v{trace.graph_version}\n"
            f"Status: {trace.status}\n"
            f"Duration: {trace.duration_ms:.1f}ms\n"
            f"Total tokens: {trace.total_tokens}\n"
            f"Nodes executed: {[r.node_id for r in trace.node_records]}\n"
            f"Final state: {trace.final_state}"
        )

        result = await agent.run(trace_summary)
        output = result.output

        return GradeResult(
            grader_id=grader.id,
            execution_id=trace.execution_id,
            score=max(0.0, min(1.0, output.score)),  # clamp to [0, 1]
            label="pass" if output.score >= 0.7 else "fail",
            reason=output.reason,
        )

    async def _grade_heuristic(
        self, trace: ExecutionTrace, grader: Grader
    ) -> GradeResult:
        """
        Evaluate trace metrics against thresholds (SPEC §12.2).

        Config format:
            {
              "max_tokens": 5000,
              "max_duration_ms": 10000,
              "max_retries": 2
            }
        All declared thresholds must pass for score=1.0.
        """
        cfg = grader.config
        failures: list[str] = []

        max_tokens = cfg.get("max_tokens")
        if max_tokens is not None and trace.total_tokens > max_tokens:
            failures.append(
                f"total_tokens ({trace.total_tokens}) > max_tokens ({max_tokens})"
            )

        max_duration = cfg.get("max_duration_ms")
        if max_duration is not None and trace.duration_ms > max_duration:
            failures.append(
                f"duration_ms ({trace.duration_ms:.1f}) > max_duration_ms ({max_duration})"
            )

        max_retries = cfg.get("max_retries")
        if max_retries is not None:
            total_retries = sum(
                max(0, r.attempt - 1) for r in trace.node_records
            )
            if total_retries > max_retries:
                failures.append(
                    f"total_retries ({total_retries}) > max_retries ({max_retries})"
                )

        passed = len(failures) == 0
        return GradeResult(
            grader_id=grader.id,
            execution_id=trace.execution_id,
            score=1.0 if passed else 0.0,
            label="pass" if passed else "fail",
            reason="All thresholds met" if passed else "; ".join(failures),
        )
