"""
Output schemas for example graphs.
These are the Pydantic models that AgentNodes validate their LLM output against.
"""
from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sales router (sales_router.yaml, sales_router_condition.yaml)
# ---------------------------------------------------------------------------

class ClassifierOutput(BaseModel):
    intent: Literal["HOT", "WARM", "COLD"]
    confidence: float


class ResponseOutput(BaseModel):
    response: str


# ---------------------------------------------------------------------------
# Evaluator loop (01_evaluator_loop.yaml)
# ---------------------------------------------------------------------------

class DraftOutput(BaseModel):
    draft: str


class EvalOutput(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    feedback: str = ""


class PublishedOutput(BaseModel):
    final_content: str


# ---------------------------------------------------------------------------
# Sequential pipeline (02_sequential_pipeline.yaml)
# ---------------------------------------------------------------------------

class ExtractorOutput(BaseModel):
    entities: str


class AnalyzerOutput(BaseModel):
    sentiment: Literal["positivo", "negativo", "neutro"]
    intent: Literal["queja", "consulta", "compra", "cancelacion", "felicitacion", "otro"]


class StrategyOutput(BaseModel):
    strategy: str


class ResponderOutput(BaseModel):
    response: str


# ---------------------------------------------------------------------------
# Supervisor (04_supervisor.yaml)
# ---------------------------------------------------------------------------

class SupervisorOutput(BaseModel):
    specialist: Literal["billing", "technical", "account", "sales", "general"]
    reasoning: str


# ---------------------------------------------------------------------------
# Error handling (05_error_handling.yaml)
# ---------------------------------------------------------------------------

class ResultOutput(BaseModel):
    result: str
