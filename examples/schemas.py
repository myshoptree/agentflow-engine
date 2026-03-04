"""
Output schemas for example graphs.
These are the Pydantic models that AgentNodes validate their LLM output against.
"""
from typing import Literal
from pydantic import BaseModel


class ClassifierOutput(BaseModel):
    intent: Literal["HOT", "WARM", "COLD"]
    confidence: float


class ResponseOutput(BaseModel):
    response: str
