"""
Tests for C2 — Graders and ExecutionTrace evaluation.

Uses mock traces to avoid real LLM calls.
SPEC references: §12.1–§12.3
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentflow.core.grader import GraderRunner
from agentflow.core.models import (
    ExecutionStatus,
    ExecutionTrace,
    Grader,
    GraderType,
    NodeExecutionRecord,
    NodeType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_trace(
    final_state: dict = None,
    total_tokens: int = 100,
    duration_ms: float = 500.0,
    node_records: list[NodeExecutionRecord] = None,
) -> ExecutionTrace:
    return ExecutionTrace(
        execution_id="test-exec-001",
        graph_id="test-graph",
        graph_version="1.0.0",
        status=ExecutionStatus.COMPLETED,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        node_records=node_records or [],
        checkpoints=[],
        final_state=final_state or {},
    )


def make_record(node_id: str, attempt: int = 1) -> NodeExecutionRecord:
    return NodeExecutionRecord(
        execution_id="test-exec-001",
        node_id=node_id,
        node_type=NodeType.TOOL,
        attempt=attempt,
        input_state={},
        started_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Deterministic grader
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_grader_pass():
    """Deterministic grader gives score=1.0 when condition matches."""
    trace = make_trace(final_state={"intent": "HOT"})
    grader = Grader(
        id="g1",
        name="Intent Check",
        type=GraderType.DETERMINISTIC,
        config={"field": "state.intent", "operator": "eq", "value": "HOT"},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 1.0
    assert result.label == "pass"


@pytest.mark.asyncio
async def test_deterministic_grader_fail():
    """Deterministic grader gives score=0.0 when condition doesn't match."""
    trace = make_trace(final_state={"intent": "COLD"})
    grader = Grader(
        id="g2",
        name="Intent Check",
        type=GraderType.DETERMINISTIC,
        config={"field": "state.intent", "operator": "eq", "value": "HOT"},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 0.0
    assert result.label == "fail"


@pytest.mark.asyncio
async def test_deterministic_grader_numeric():
    """Deterministic grader works with numeric comparisons."""
    trace = make_trace(final_state={"score": 0.9})
    grader = Grader(
        id="g3",
        name="Score Check",
        type=GraderType.DETERMINISTIC,
        config={"field": "state.score", "operator": "gte", "value": 0.8},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_deterministic_grader_error_on_bad_config():
    """Deterministic grader handles evaluation error gracefully."""
    trace = make_trace(final_state={})  # missing the field
    grader = Grader(
        id="g4",
        name="Missing Field",
        type=GraderType.DETERMINISTIC,
        config={"field": "state.missing_field", "operator": "eq", "value": "HOT"},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 0.0
    assert result.label == "error"


# ---------------------------------------------------------------------------
# Heuristic grader
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heuristic_grader_passes_all_thresholds():
    """Heuristic grader passes when all metrics are within thresholds."""
    trace = make_trace(total_tokens=100, duration_ms=500.0)
    grader = Grader(
        id="h1",
        name="Performance",
        type=GraderType.HEURISTIC,
        config={"max_tokens": 5000, "max_duration_ms": 10000},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 1.0
    assert result.label == "pass"


@pytest.mark.asyncio
async def test_heuristic_grader_fails_on_token_excess():
    """Heuristic grader fails when total_tokens exceeds max_tokens."""
    trace = make_trace(total_tokens=6000)
    grader = Grader(
        id="h2",
        name="Token Check",
        type=GraderType.HEURISTIC,
        config={"max_tokens": 5000},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 0.0
    assert "max_tokens" in result.reason


@pytest.mark.asyncio
async def test_heuristic_grader_fails_on_duration():
    """Heuristic grader fails when duration exceeds threshold."""
    trace = make_trace(duration_ms=15000.0)
    grader = Grader(
        id="h3",
        name="Duration Check",
        type=GraderType.HEURISTIC,
        config={"max_duration_ms": 10000},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 0.0
    assert "duration_ms" in result.reason


@pytest.mark.asyncio
async def test_heuristic_grader_fails_on_retries():
    """Heuristic grader fails when retry count exceeds threshold."""
    records = [
        make_record("n1", attempt=1),
        make_record("n1", attempt=2),
        make_record("n1", attempt=3),
    ]
    trace = make_trace(node_records=records)
    grader = Grader(
        id="h4",
        name="Retry Check",
        type=GraderType.HEURISTIC,
        config={"max_retries": 1},
    )
    runner = GraderRunner()
    result = await runner.grade(trace, grader)
    assert result.score == 0.0
    assert "retries" in result.reason


# ---------------------------------------------------------------------------
# GradeResult immutability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grade_result_does_not_modify_trace():
    """Grader must not mutate the trace (SPEC §12.3)."""
    trace = make_trace(final_state={"intent": "HOT"})
    original_state = dict(trace.final_state)
    original_records_count = len(trace.node_records)

    grader = Grader(
        id="immutability",
        name="Immutability Check",
        type=GraderType.DETERMINISTIC,
        config={"field": "state.intent", "operator": "eq", "value": "HOT"},
    )
    runner = GraderRunner()
    await runner.grade(trace, grader)

    assert trace.final_state == original_state
    assert len(trace.node_records) == original_records_count


# ---------------------------------------------------------------------------
# State manager integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_get_grades():
    """State manager should persist and retrieve GradeResults."""
    from agentflow.core.models import GradeResult
    from agentflow.core.state_manager import InMemoryStateManager

    sm = InMemoryStateManager()
    grade = GradeResult(
        grader_id="g1",
        execution_id="exec-001",
        score=1.0,
        label="pass",
        reason="all good",
    )

    # Manually set up the grades dict (normally populated by get_trace)
    sm._grades["exec-001"] = []
    await sm.save_grade(grade)

    results = await sm.get_grades("exec-001")
    assert len(results) == 1
    assert results[0].score == 1.0
    assert results[0].grader_id == "g1"
