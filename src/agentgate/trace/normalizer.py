"""spans -> NormalizedTrace normalization (the eval-consumable execution graph).

Supports two span conventions:
  1. fin-runtime private convention: agent.run / llm.call / tool.execute
     (attrs: llm.model, llm.usage.total_tokens, tool.name, tool.status)
  2. openJiuwen native convention (gen_ai.* / openjiuwen.* semantics, T3 protocol-level):
     llm.<ClassName> (CLIENT, attrs: gen_ai.request.model, gen_ai.usage.prompt_tokens/
     completion_tokens), tool.<name> (attrs: gen_ai.tool.name, openjiuwen.status);
     other internal spans are treated as steps.
"""
from typing import Dict, List

from .models import NormalizedTrace, TraceStep

_TOOL_ATTR_ALIASES = ("tool.name", "gen_ai.tool.name")


def _step_kind(span_name: str, attrs: Dict) -> str:
    if span_name == "tool.execute":
        return "tool"
    if span_name == "llm.call":
        return "llm"
    # openJiuwen native: LLM spans carry usage/model semantic attrs; tool spans carry gen_ai.tool.name
    if "gen_ai.usage.prompt_tokens" in attrs or "gen_ai.usage.total_tokens" in attrs:
        return "llm"
    if span_name.startswith("tool.") and ("gen_ai.tool.name" in attrs
                                          or attrs.get("gen_ai.operation.name") == "execute_tool"):
        return "tool"
    return "step"


def _tool_name(attrs: Dict, span_name: str) -> str:
    for key in _TOOL_ATTR_ALIASES:
        if attrs.get(key):
            return str(attrs[key])
    return span_name.split(".")[-1] if "." in span_name else span_name


def _tool_status(kind: str, attrs: Dict) -> str:
    if kind != "tool":
        return "ok"
    status = attrs.get("tool.status")
    if status is not None:
        return str(status)
    oj = str(attrs.get("openjiuwen.status", "finish"))
    return "error" if oj not in ("finish", "ok", "success") else "ok"


def normalize(spans: List[Dict]) -> NormalizedTrace:
    spans = sorted(spans or [], key=lambda s: s.get("start_unix_nano", 0))
    steps: List[TraceStep] = []
    root: Dict = {}
    tool_calls: List[str] = []
    executed: List[str] = []
    denied: List[str] = []
    usage = 0
    model = ""
    for i, s in enumerate(spans):
        attrs = s.get("attributes", {}) or {}
        name = s.get("name", "")
        kind = _step_kind(name, attrs)
        status = _tool_status(kind, attrs)
        steps.append(TraceStep(idx=i, kind=kind, name=name, status=status, attrs=attrs))
        if name == "agent.run":
            root = attrs
        if kind == "tool":
            tn = _tool_name(attrs, name)
            tool_calls.append(tn)
            if status == "denied":
                denied.append(tn)
            else:
                executed.append(tn)
        if kind == "llm":
            usage += int(attrs.get("llm.usage.total_tokens", 0) or 0)
            usage += (int(attrs.get("gen_ai.usage.prompt_tokens", 0) or 0)
                      + int(attrs.get("gen_ai.usage.completion_tokens", 0) or 0))
            model = model or str(attrs.get("llm.model", "")
                                 or attrs.get("gen_ai.request.model", ""))
    if not model:
        model = str(root.get("model", ""))
    trace_id = str(root.get("trace.id") or (spans[0].get("trace_id", "") if spans else ""))
    agent_id = str(root.get("agent.id") or root.get("service.name", ""))
    if not agent_id:  # openJiuwen paths have no agent.run root: service.name from the resource is on every span
        agent_id = next((str(s["attributes"].get("service.name"))
                         for s in spans if (s.get("attributes") or {}).get("service.name")), "")
    return NormalizedTrace(
        trace_id=trace_id,
        agent_id=agent_id,
        model=model,
        component_versions=str(root.get("component.version", "")),
        steps=steps, tool_calls=tool_calls, executed_tools=executed,
        denied_tools=denied, usage_tokens=usage,
        source=str(root.get("source", "eval")),
    )
