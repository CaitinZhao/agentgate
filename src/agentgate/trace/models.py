"""Normalized trace models: the eval-consumable execution graph projected from OTel spans."""
from typing import Dict, List

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    idx: int
    kind: str                                  # tool / llm / step
    name: str                                  # tool.execute / llm.call / span name
    status: str = "ok"                         # ok / denied / error
    attrs: Dict = Field(default_factory=dict)


class NormalizedTrace(BaseModel):
    trace_id: str = ""
    agent_id: str = ""
    model: str = ""
    component_versions: str = ""               # e.g. bank-report-collector@0.1.0
    steps: List[TraceStep] = Field(default_factory=list)
    tool_calls: List[str] = Field(default_factory=list)      # all tool call names (incl. blocked)
    executed_tools: List[str] = Field(default_factory=list)  # actually executed successfully
    denied_tools: List[str] = Field(default_factory=list)    # blocked by red lines
    usage_tokens: int = 0
    source: str = "eval"
